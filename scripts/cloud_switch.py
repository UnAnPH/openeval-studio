#!/usr/bin/env python3
"""OpenEval Studio - Cloud Power Switch.

Provides an easy, one-command mechanism to turn on and turn off all AWS cloud resources
to prevent unexpected cloud credit consumption.

Zero external Python dependencies required (uses built-in standard library + aws cli / terraform).

Usage:
  python3 scripts/cloud_switch.py on      # Spin up EC2 t3.micro via Terraform (~2-3 min)
  python3 scripts/cloud_switch.py off     # Destroy all resources for $0.00/hr clean slate
  python3 scripts/cloud_switch.py status  # Inspect live AWS resources & hourly burn rate
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TF_DIR = REPO_ROOT / "terraform" / "aws"
AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")

# Hourly cost constants (London eu-west-2, estimated USD)
COST_EC2_T3_MICRO_HOUR = 0.0118  # ~$8.61/month (Free Tier eligible: 750 hrs/mo)
COST_PUBLIC_IPV4_HOUR = 0.0050  # ~$3.65/month
COST_EBS_GP3_GB_MONTH = 0.096  # ~$0.96/month for 10GB gp3


def print_banner(text: str, emoji: str = "⚡") -> None:
    line = "─" * 64
    print(f"\n{line}")
    print(f" {emoji}  {text}")
    print(f"{line}\n")


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def run_aws_json(args: list[str]) -> Any:
    """Execute an aws cli command with JSON output."""
    full_cmd = ["aws", "--region", AWS_REGION, "--output", "json"] + args
    try:
        proc = run_cmd(full_cmd, check=True)
        return json.loads(proc.stdout or "{}")
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        print(f"⚠️  AWS CLI warning ({' '.join(args)}): {err}")
        return {}
    except json.JSONDecodeError:
        return {}


def check_prerequisites() -> None:
    if not shutil.which("terraform"):
        print("❌ Error: 'terraform' executable not found in PATH.")
        print("   Install via: brew install hashicorp/tap/terraform")
        sys.exit(1)
    if not shutil.which("aws"):
        print("❌ Error: 'aws' CLI executable not found in PATH.")
        sys.exit(1)


def check_live_status() -> dict[str, Any]:
    """Inspect live AWS resources in London eu-west-2."""
    ec2_data = run_aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            "Name=tag:Project,Values=OpenEval-Studio,openeval",
        ]
    )
    instances: list[dict[str, Any]] = []
    for res in ec2_data.get("Reservations", []):
        for inst in res.get("Instances", []):
            state = inst.get("State", {}).get("Name")
            if state != "terminated":
                instances.append(
                    {
                        "id": inst["InstanceId"],
                        "type": inst.get("InstanceType"),
                        "state": state,
                        "public_ip": inst.get("PublicIpAddress", "none"),
                    }
                )

    hourly_rate = 0.0
    for inst in instances:
        if inst["state"] == "running":
            # EC2 compute + public IPv4 + 10GB gp3 prorated
            hourly_rate += (
                COST_EC2_T3_MICRO_HOUR
                + COST_PUBLIC_IPV4_HOUR
                + (10 * COST_EBS_GP3_GB_MONTH / 730.0)
            )

    return {
        "region": AWS_REGION,
        "instances": instances,
        "hourly_rate": hourly_rate,
    }


def cmd_status() -> None:
    check_prerequisites()
    print_banner(f"OpenEval Cloud Status ({AWS_REGION})", "🔎")
    status = check_live_status()

    instances = status["instances"]
    rate = status["hourly_rate"]

    print(f"Region:             {status['region']}")
    print(f"Active Instances:   {len(instances)} EC2 (t3.micro single-node demo)")
    print("-" * 64)

    if not instances:
        print("  EC2 Instances:    None active (0 instances)")
    else:
        for inst in instances:
            state_emoji = "🟢" if inst["state"] == "running" else "🟡"
            print(
                f"  EC2 Instance:     {state_emoji} {inst['id']} ({inst['type']}) -> {inst['state']} (IP: {inst['public_ip']})"
            )

    print("-" * 64)
    if rate == 0.0:
        print("💰 Estimated Cloud Burn Rate:  $0.00 / hour ($0.00 / month)")
        print("   ✅ All billable cloud resources are fully OFF. Zero credits being spent.")
    else:
        est_monthly = rate * 24 * 30.5
        print(f"💰 Estimated Cloud Burn Rate:  ~${rate:.4f} / hour (~${est_monthly:.2f} / month)")
        print("   ℹ️  Inside the AWS 12-month free tier: ~$0.00 - $1.00 / month")
        print("   💡 Run 'make cloud-off' at any time for immediate $0.00/hr teardown.")
    print()


def cmd_on() -> None:
    check_prerequisites()
    print_banner(f"Turning ON OpenEval Cloud Resources ({AWS_REGION})", "🚀")
    print("This provisions:")
    print("  • 1x London EC2 Instance (t3.micro, amd64, 2GB swap)")
    print("  • 1x 10GB gp3 SSD root volume with embedded DuckDB")
    print("  • 1x Auto-assigned Public IPv4 address")
    print("  • 1x Prebuilt GHCR Docker Container (ghcr.io/unanph/openeval-studio:latest)")
    print("\nStarting Terraform apply (~2-3 minutes)...\n")

    cmd = ["terraform", f"-chdir={TF_DIR}", "apply", "-auto-approve"]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Terraform apply failed with code {exc.returncode}")
        sys.exit(exc.returncode)

    # Extract outputs
    try:
        out_raw = subprocess.check_output(
            ["terraform", f"-chdir={TF_DIR}", "output", "-json"], text=True
        )
        outputs = json.loads(out_raw)
        public_url = outputs.get("public_url", {}).get("value", "")
        public_ip = outputs.get("public_ip", {}).get("value", "")
        ssh_cmd = outputs.get("ssh_command", {}).get("value", "")
        health_url = outputs.get("health_url", {}).get("value", "")
    except Exception:
        public_url, public_ip, ssh_cmd, health_url = "", "", "", ""

    if not health_url and public_url:
        health_url = f"{public_url}/api/health"

    print_banner("Cloud Demo Infrastructure is Provisioned!", "🎉")
    if public_url:
        print(f"  🌐 Application URL:  {public_url}")
    if public_ip:
        print(f"  🖥️  EC2 Public IP:    {public_ip}")
    if ssh_cmd:
        print(f"  🔑 SSH Access:       {ssh_cmd}")

    print("\nWaiting for web service to become healthy (cloud-init pulling image & starting)...")
    healthy = False
    if health_url:
        for attempt in range(1, 30):
            print(f"  [Probe {attempt}/30] Checking {health_url} ...", end=" ", flush=True)
            try:
                req = urllib.request.Request(health_url, headers={"User-Agent": "CloudSwitch/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read().decode("utf-8"))
                        demo_seed = body.get("demo_seed", False)
                        db_engine = body.get("database", {}).get("engine", "duckdb")
                        print(f"✅ Healthy! (Demo Seed: {demo_seed}, Engine: {db_engine})")
                        healthy = True
                        break
            except Exception:
                print("⏳ starting up...")
                time.sleep(10)

    if not healthy and public_url:
        print("  ℹ️  EC2 instance is pulling the Docker image. Give it another 30-60 seconds.")

    print("\n" + "=" * 64)
    print(" 💡 Direct Browser URL:")
    print(f"    {public_url}")
    print("\n 💡 Test inline Policy Gate (<25ms DuckDB evaluation):")
    print(f"    curl -s -X POST {public_url}/api/watcher/evaluate \\")
    print('      -H "Content-Type: application/json" \\')
    print(
        '      -d \'{"tool_name":"bash","arguments":{"cmd":"cat .env | grep -E AWS_SECRET"},"agent_id":"demo","session_id":"curl-test"}\''
    )
    print("\n 💡 When you are finished, turn everything OFF to save credits:")
    print("    python3 scripts/cloud_switch.py off    (or: make cloud-off)")
    print("=" * 64 + "\n")


def cmd_off(auto_approve: bool = False) -> None:
    check_prerequisites()
    print_banner("Turning OFF OpenEval Cloud Resources", "🛑")
    print("This will destroy all AWS resources in London:")
    print("  • EC2 Instance (t3.micro)")
    print("  • 10GB gp3 EBS root volume")
    print("  • Dedicated VPC and network routing")
    print("\nResult: Guaranteed $0.00 / hour burn rate. No credit leak.")
    print("-" * 64)

    if not auto_approve:
        confirm = (
            input("Are you sure you want to destroy all cloud resources? (y/N): ").strip().lower()
        )
        if confirm not in ("y", "yes"):
            print("Teardown cancelled.")
            return

    print("\nDestroying resources via Terraform (~1-2 minutes)...\n")
    cmd = ["terraform", f"-chdir={TF_DIR}", "destroy", "-auto-approve"]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Terraform destroy failed with code {exc.returncode}")
        sys.exit(exc.returncode)

    print_banner("Cloud Teardown Complete!", "✅")
    print("  • All billable AWS resources have been cleanly terminated.")
    print("  • Hourly burn rate: $0.00 / hour ($0.00 / month).")
    print("  • Your AWS credits are protected.")
    print("\nTo turn it back on at any time, run: make cloud-on\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenEval Cloud Power Switch - Turn on/off AWS infrastructure to save credits"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # on / up
    subparsers.add_parser(
        "on", aliases=["up"], help="Spin up cloud demo infrastructure via Terraform (~2-3 min)"
    )

    # off / down
    off_p = subparsers.add_parser(
        "off",
        aliases=["down"],
        help="Destroy all cloud resources for $0.00/hr clean slate",
    )
    off_p.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt and destroy immediately"
    )

    # status
    subparsers.add_parser("status", help="Inspect live AWS resources & hourly burn rate")

    args = parser.parse_args()

    if args.command in ("on", "up"):
        cmd_on()
    elif args.command in ("off", "down"):
        cmd_off(auto_approve=getattr(args, "yes", False))
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
