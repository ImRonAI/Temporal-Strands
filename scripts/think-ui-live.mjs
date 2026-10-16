#!/usr/bin/env node
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

const outDir = join(process.cwd(), "output/playwright/agent-response");
mkdirSync(outDir, { recursive: true });

const prompt = [
  "You MUST call think first on this turn with cycle_count=2 and system_prompt: You are a systems architect.",
  "Inside think, call list_agent_models once, then compare queue vs stack in 3 sentences.",
  "After think, one sentence confirming list_agent_models ran inside think.",
].join(" ");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

await page.goto("http://localhost:3001/", { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(2000);

const picker = page.locator("button").filter({ hasText: /preset|fable|gemini|High/i }).first();
await picker.click({ timeout: 20000 });
const fable = page.getByRole("option", { name: /fable/i });
if ((await fable.count()) > 0) {
  await fable.first().click();
} else {
  await page.getByRole("option").first().click();
}
await page.waitForTimeout(500);
const selectedModel = ((await picker.textContent()) || "").trim();

await page.evaluate(() => {
  window.__thinkUiEvidence = { cotSteps: [], tools: [], errors: [] };
  new MutationObserver(() => {
    const ev = window.__thinkUiEvidence;
    for (const btn of document.querySelectorAll("button")) {
      const label = btn.textContent || "";
      if (!/Thinking|Chain of Thought/i.test(label)) continue;
      let chain = btn.parentElement;
      for (let i = 0; i < 4 && chain; i++) chain = chain.parentElement;
      if (!chain) continue;
      const text = [...chain.querySelectorAll("p, pre, [data-streamdown]")]
        .map((el) => el.textContent?.trim())
        .filter(Boolean)
        .join("\n");
      if (text && ev.cotSteps.at(-1) !== text) ev.cotSteps.push(text.slice(0, 5000));
    }
    for (const el of document.querySelectorAll("button, span, h3, p")) {
      const t = el.textContent?.trim();
      if (t && /list_agent_models|^think$|Cycle \d/i.test(t) && !ev.tools.includes(t)) {
        ev.tools.push(t.slice(0, 120));
      }
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
});

const composer = page.getByRole("textbox").first();
await composer.fill(prompt);
await page.getByRole("button", { name: /Submit|Send/i }).click();

let timedOut = false;
try {
  await page.waitForFunction(
    () => {
      const ev = window.__thinkUiEvidence;
      const hasCycle = ev.cotSteps.some((t) => /Cycle \d+\/\d+/i.test(t));
      const hasList = ev.tools.some((t) => /list_agent_models/i.test(t));
      return hasCycle || hasList || ev.cotSteps.length >= 2;
    },
    null,
    { timeout: 180000 },
  );
} catch {
  timedOut = true;
}

await page.waitForTimeout(8000);
const evidence = await page.evaluate(() => window.__thinkUiEvidence || {});
const bodyText = (await page.locator("body").textContent()) || "";
const errMatch = bodyText.match(/(ThinkActivityRetired|first tool call must be think|ActivityError[^\n]{0,120})/i);

const result = {
  timedOut,
  selectedModel,
  evidence,
  hasCycleInCot: (evidence.cotSteps || []).some((t) => /Cycle \d+\/\d+/i.test(t)),
  hasListAgentModels:
    (evidence.tools || []).some((t) => /list_agent_models/i.test(t)) ||
    /list_agent_models/i.test(bodyText),
  hasThinkingHeader: /Thinking|Chain of Thought/i.test(bodyText),
  workflowError: errMatch ? errMatch[0] : null,
  bodySnippet: bodyText.slice(0, 3500),
};

writeFileSync(join(outDir, "think-orchestrator-parent-ui.json"), JSON.stringify(result, null, 2));
await page.screenshot({ path: join(outDir, "think-orchestrator-parent-ui.png"), fullPage: true });
await browser.close();

console.log(JSON.stringify(result, null, 2));
process.exit(
  result.hasCycleInCot && result.hasThinkingHeader && !result.workflowError ? 0 : 1,
);
