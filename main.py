import argparse, json
from orchestrator import Coordinator
import state as st

def main():
    p = argparse.ArgumentParser(description="Community Emergency Coordination Agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("text")
    x = sub.add_parser("resolve"); x.add_argument("incident_id"); x.add_argument("--notes", default="")
    a = sub.add_parser("ask"); a.add_argument("question")
    sub.add_parser("status"); sub.add_parser("reset")
    args = p.parse_args()
    if args.cmd == "reset":
        st.reset(); print("State reset."); return
    c = Coordinator(); print("Backends:", c.status())
    if args.cmd == "report":
        out = c.handle_report(args.text)
        for step, info in out["trace"]: print(f"  [{step}] {info}")
        print(json.dumps({k: v for k, v in out.items() if k in ("resources", "volunteers")}, indent=2, ensure_ascii=False))
    elif args.cmd == "resolve": print(c.resolve(args.incident_id, args.notes))
    elif args.cmd == "ask": print(c.ask(args.question))
    else:
        s = st.load()
        for i in s["incidents"]: print(i["id"], i["status"], i["type"], i["location"], f"sev{i['severity']}")

if __name__ == "__main__":
    main()
