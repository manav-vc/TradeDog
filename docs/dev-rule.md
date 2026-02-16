### Simplest starting system (GitHub Issues + Milestones)

1. **Create Milestones (these are your big phases):**
   - v0.1 – Minimal end-to-end loop (manual trigger, 1 ticker, basic buy + guardian sell)
   - v0.2 – Scheduler + Profit Guardian polish
   - v0.3 – Multi-ticker + Dashboard
   - Backtesting foundation
   - Experiments & Research

2. **Create Issue labels (keep to 6–8 max):**
   - priority:high / priority:medium
   - area:graph / area:agents / area:tools / area:data / area:ui / area:config
   - type:bug / type:feature / type:refactor / type:experiment
   - status:blocked (use when waiting on API key, etc.)

3. **Workflow for a typical work session:**
   - Open repo → look at Milestone “v0.1”
   - Pick 1–3 small issues (ideally < 2–4 hours each)
   - Create branch: `git checkout -b 47-add-profit-guardian-node`
   - Work → commit often with conventional messages:
     ```
     feat: add initial profit guardian node with take-profit logic
     refactor: extract current_price helper to tools/
     fix: handle division by zero in profit pct calc
     ```
   - When done → create Pull Request → link the issue (`closes #47`)
   - Merge (you can squash or rebase — solo so whatever feels clean)


