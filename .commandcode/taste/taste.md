## Communication
- User communicates in Chinese. Confidence: 0.95

## Workflow
- Executes git operations (commit and push) sequentially and immediately upon request, rather than asking for intermediate confirmations or message drafts. Confidence: 0.85
- Is highly cost-sensitive about GitHub Actions minutes (90 min/run, 2000 min/month quota) and prefers CI optimizations: reduced cron frequency, build caching, and skipping builds when upstream is unchanged. Confidence: 0.90
- Prefers to keep changes local without auto-pushing when instructed "不用推送", continuing to next tasks and deferring push until explicitly requested. Confidence: 0.80
