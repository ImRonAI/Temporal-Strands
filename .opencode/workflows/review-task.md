# Review Active Task

1. Require active state `controller_verified` or later and current GREEN evidence.
2. Delegate to `spec-reviewer`; include task ID only, not a leading opinion.
3. Record approval or findings. If changes are requested, set `implementing`, delegate fixes, and repeat controller verification.
4. After spec approval, set `spec_approved` and delegate independently to `quality-reviewer`.
5. Record approval or findings. If changes are requested, set `implementing`, delegate fixes, and repeat both reviews after controller verification.
6. Set `quality_approved`, then `completed`, only when both current review results approve the same verified diff.
