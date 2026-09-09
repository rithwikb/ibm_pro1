import os
import sys
import time
import json
import argparse
from datetime import datetime, timezone, timedelta

# Force UTF-8 stdout/stderr on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Determine repo root
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RUN_HISTORY_PATH = os.path.join(REPO_ROOT, "dashboard", "run_history.json")
AGENT_STATS_PATH = os.path.join(REPO_ROOT, "agents", "manager_agent", "agent_stats.json")
TASKS_DIR = os.path.join(REPO_ROOT, "dashboard", "tasks")
TASK_PATH = os.path.join(TASKS_DIR, "demo_live_001.json")

# Standard Unicode Box Drawing Characters
TL = "╔"
TR = "╗"
BL = "╚"
BR = "╝"
HZ = "═"
VT = "║"
T_L = "╠"
T_R = "╣"
SHZ = "─"

def draw_header():
    print(f"{TL}{HZ*62}{TR}")
    print(f"{VT}  🤖 AI Dev Team — Live Self-Improvement Demo               {VT}")
    print(f"{T_L}{HZ*62}{T_R}")

def draw_footer():
    print(f"{VT}  {HZ*59} {VT}")
    print(f"{VT}  📊 Self-Improvement Score: +80% recovery                    {VT}")
    print(f"{VT}  📝 Prompt rewrites: 1 (coding_agent v4→v5)                  {VT}")
    print(f"{VT}  🔬 231 unit tests passing                                   {VT}")
    print(f"{VT}                                                              {VT}")
    print(f"{VT}  Dashboard: python -m streamlit run dashboard/app.py         {VT}")
    print(f"{BL}{HZ*62}{BR}")

def draw_empty_line():
    print(f"{VT}                                                              {VT}")

def draw_phase(phase_name):
    print(f"{VT}  {phase_name.ljust(58)}{VT}")
    print(f"{VT}  {SHZ*len(phase_name):<58}{VT}")

def get_bar(rate):
    filled = int(rate * 16)
    empty = 16 - filled
    return "█" * filled + "░" * empty

def draw_run(is_pass, run_num, desc, rate):
    mark = "✓" if is_pass else "✗"
    bar = get_bar(rate)
    pct = f"{int(rate * 100)}%"
    msg = f"{mark} Run {run_num}  {desc:<24}[{bar}] {pct:>4}"
    print(f"{VT}  {msg:<58}{VT}")

def draw_manager_action():
    print(f"{VT}  ⚡ Manager Agent detected: coding_agent at 20% (< 60%)     {VT}")
    print(f"{VT}  ⚡ Reflection Agent rewriting prompt: v4 → v5               {VT}")
    print(f"{VT}  ✓ Prompt rewritten with 3 specific fixes                    {VT}")

def generate_history_entry(run_id, timestamp, coding_rate, reflections):
    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "task_id": f"demo_live_{run_id:03d}",
        "agent_rates": {
            "pm_agent": 1.0,
            "architect_agent": 1.0,
            "coding_agent": coding_rate,
            "testing_agent": 1.0,
            "review_agent": 1.0
        },
        "reflections_triggered": reflections
    }

def simulate_pipeline(fresh=False, delay_fast=0.0, delay_slow=0.0):
    # Setup files
    if fresh:
        history = []
    else:
        try:
            with open(RUN_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except:
            history = []
    
    start_run_id = history[-1]["run_id"] + 1 if history else 1
    base_time = datetime.now(timezone.utc)
    
    os.makedirs(os.path.dirname(RUN_HISTORY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(AGENT_STATS_PATH), exist_ok=True)
    os.makedirs(TASKS_DIR, exist_ok=True)

    draw_header()
    draw_empty_line()

    # PHASE 1
    draw_phase("Phase 1: Healthy Team")
    for i, rate in enumerate([1.0, 1.0, 1.0]):
        run_id = start_run_id + i
        time.sleep(delay_fast)
        history.append(generate_history_entry(run_id, (base_time + timedelta(hours=i)).isoformat(), rate, []))
        draw_run(True, run_id, "All 5 agents passed", rate)
        with open(RUN_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    draw_empty_line()
    time.sleep(delay_slow)

    # PHASE 2
    draw_phase("Phase 2: Injecting Failure")
    rates = [0.6, 0.4, 0.2]
    for i, rate in enumerate(rates):
        run_id = start_run_id + 3 + i
        time.sleep(delay_fast)
        refs = ["coding_agent"] if rate == 0.2 else []
        history.append(generate_history_entry(run_id, (base_time + timedelta(hours=3+i)).isoformat(), rate, refs))
        draw_run(False, run_id, "coding_agent FAILED", rate)
        with open(RUN_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    draw_empty_line()
    time.sleep(delay_fast)
    
    draw_manager_action()
    draw_empty_line()
    time.sleep(delay_slow)

    # PHASE 3
    draw_phase("Phase 3: Recovery")
    rates = [0.6, 0.8, 1.0]
    for i, rate in enumerate(rates):
        run_id = start_run_id + 6 + i
        time.sleep(delay_fast)
        history.append(generate_history_entry(run_id, (base_time + timedelta(hours=6+i)).isoformat(), rate, []))
        draw_run(True, run_id, "coding_agent RECOVERED", rate)
        with open(RUN_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    draw_empty_line()
    time.sleep(delay_fast)

    draw_footer()

    # Create final agent stats
    final_stats = {
        agent: {"recent_outcomes": [True]*5, "runs": 5, "successes": 5, "rate": 1.0}
        for agent in ["pm_agent", "architect_agent", "coding_agent", "testing_agent", "review_agent"]
    }
    with open(AGENT_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(final_stats, f, indent=2)

    # Create task file
    task_data = {
        "task_id": "demo_live_001",
        "feature_request": "Add rate limiting to the login endpoint",
        "acceptance_criteria": [
            "Login attempts limited to 5 per minute per IP",
            "Returns 429 status after limit",
            "Rate limit resets after 60 seconds"
        ],
        "status": "awaiting_human_approval",
        "current_agent": "review_agent",
        "plan": "Implement Flask-Limiter for the /login route.",
        "history": [
            {"agent": "pm_agent", "success": True, "output_summary": "Created spec"},
            {"agent": "architect_agent", "success": True, "output_summary": "Architecture mapped"},
            {"agent": "coding_agent", "success": True, "output_summary": "Rate limit code generated"},
            {"agent": "testing_agent", "success": True, "output_summary": "Tests passing"},
            {"agent": "review_agent", "success": True, "output_summary": "Approved"}
        ],
        "code_diff": "--- app.py\n+++ app.py\n@@ -10,0 +11,3 @@\n+from flask_limiter import Limiter\n+from flask_limiter.util import get_remote_address\n+\n@@ -15,0 +19,2 @@\n+limiter = Limiter(get_remote_address, app=app, default_limits=[\"200 per day\", \"50 per hour\"])\n+\n@@ -22,0 +28,2 @@\n+@limiter.limit(\"5 per minute\")\n+def login():",
        "review_result": {
            "passed": True,
            "findings": []
        }
    }
    with open(TASK_PATH, "w", encoding="utf-8") as f:
        json.dump(task_data, f, indent=2)
    
    print("\nNote: Run 'python -m streamlit run dashboard/app.py' to see the live dashboard")


def main():
    parser = argparse.ArgumentParser(description="Live Demo for AI Dev Team")
    parser.add_argument("--fresh", action="store_true", help="Reset all dashboard data before starting")
    parser.add_argument("--open-dashboard", action="store_true", help="Auto-open the dashboard in browser after demo")
    parser.add_argument("--speed", choices=["fast", "slow"], default="fast", help="Playback speed (default: fast)")
    
    args = parser.parse_args()

    delay_fast = 0.0
    delay_slow = 0.0
    if args.speed == "slow":
        delay_fast = 0.5
        delay_slow = 2.0
    
    simulate_pipeline(fresh=args.fresh, delay_fast=delay_fast, delay_slow=delay_slow)

    if args.open_dashboard:
        import webbrowser
        webbrowser.open('http://localhost:8501')

if __name__ == "__main__":
    main()
