#!/usr/bin/env python3
"""OpenEval Studio - Cloud Power Switch.

Provides an easy, one-command mechanism to turn on and turn off all AWS cloud resources
to prevent unexpected cloud credit consumption, with support for Static Elastic IP
and instant Stop/Start sleep mode.

Usage:
  python3 scripts/cloud_switch.py on       # Wake up EC2 instance (~15s) or provision via Terraform
  python3 scripts/cloud_switch.py off      # Put EC2 to sleep ($0.00/hr compute, preserves static IP)
  python3 scripts/cloud_switch.py destroy  # Complete teardown of all resources via Terraform ($0.00/mo)
  python3 scripts/cloud_switch.py status   # Inspect live AWS resources, static IP, & burn rate
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
COST_PUBLIC_IPV4_HOUR = 0.0050  # ~$3.65/month (AWS dedicated public IPv4 fee)
COST_EBS_GP3_GB_MONTH = 0.096  # ~$0.96/month for 10GB gp3 root volume


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
    eip_data = run_aws_json(
        [
            "ec2",
            "describe-addresses",
            "--filters",
            "Name=tag:Project,Values=OpenEval-Studio,openeval",
        ]
    )

    static_eip = None
    addresses = eip_data.get("Addresses", [])
    if addresses:
        static_eip = addresses[0].get("PublicIp")

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
                        "public_ip": static_eip or inst.get("PublicIpAddress", "none"),
                    }
                )

    hourly_rate = 0.0
    storage_rate = 10 * COST_EBS_GP3_GB_MONTH / 730.0  # ~$0.0013/hr (~$0.96/mo)

    if instances:
        active_inst = instances[0]
        if active_inst["state"] == "running":
            # Running: Compute + Static IPv4 + 10GB gp3 storage
            hourly_rate = COST_EC2_T3_MICRO_HOUR + COST_PUBLIC_IPV4_HOUR + storage_rate
        elif active_inst["state"] == "stopped":
            # Stopped (Sleep): Compute is $0.00! Only Static IPv4 + storage remain
            hourly_rate = COST_PUBLIC_IPV4_HOUR + storage_rate
    elif static_eip:
        # EIP allocated without instance
        hourly_rate = COST_PUBLIC_IPV4_HOUR

    return {
        "region": AWS_REGION,
        "instances": instances,
        "static_eip": static_eip,
        "hourly_rate": hourly_rate,
    }


def wait_for_health(health_url: str, max_attempts: int = 15) -> bool:
    print(f"\nVerifying application health check ({health_url})...")
    for attempt in range(1, max_attempts + 1):
        print(f"  [Probe {attempt}/{max_attempts}] Checking {health_url} ...", end=" ", flush=True)
        try:
            req = urllib.request.Request(health_url, headers={"User-Agent": "CloudSwitch/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read().decode("utf-8"))
                    if body.get("db") == "connected" and body.get("status") == "ok":
                        demo_seed = body.get("demo_seed", False)
                        print(f"✅ Healthy! (Status: ok, DB: connected, Demo Seed: {demo_seed})")
                        return True
        except Exception:
            print("⏳ warming up...")
            time.sleep(3)
    return False


def cmd_status() -> None:
    check_prerequisites()
    print_banner(f"OpenEval Cloud Status ({AWS_REGION})", "🔎")
    status = check_live_status()

    instances = status["instances"]
    rate = status["hourly_rate"]
    static_eip = status["static_eip"]

    print(f"Region:             {status['region']}")
    print(f"Static Elastic IP:  {static_eip or 'None allocated'}")
    print(f"Active Instances:   {len(instances)} EC2 (t3.micro)")
    print("-" * 64)

    if not instances:
        print("  EC2 Instances:    None active (0 instances)")
    else:
        for inst in instances:
            state_emoji = (
                "🟢"
                if inst["state"] == "running"
                else ("🟡" if inst["state"] == "stopped" else "⚪")
            )
            print(
                f"  EC2 Instance:     {state_emoji} {inst['id']} ({inst['type']}) -> {inst['state']} (IP: {inst['public_ip']})"
            )

    print("-" * 64)
    if rate == 0.0:
        print("💰 Estimated Cloud Burn Rate:  $0.00 / hour ($0.00 / month)")
        print("   ✅ All cloud resources are terminated.")
    else:
        est_monthly = rate * 24 * 30.5
        is_stopped = instances and instances[0]["state"] == "stopped"
        if is_stopped:
            print(
                f"💰 Sleeping Burn Rate:         ~${rate:.4f} / hour (~${est_monthly:.2f} / month, ~$0.15 / day)"
            )
            print(
                "   💤 Compute is $0.00/hour. Cost is solely for the dedicated Elastic IP + EBS storage."
            )
            print("   💡 Run 'make cloud-on' anytime to resume instantly in ~15 seconds.")
        else:
            print(
                f"💰 Running Burn Rate:          ~${rate:.4f} / hour (~${est_monthly:.2f} / month)"
            )
            print(
                "   ℹ️  Always-on cost in London: ~$13–$15 / month (t3.micro + Elastic IP + 10GB gp3)"
            )
            print(
                "   💡 Run 'make cloud-off' to sleep ($0.00/hr compute) or 'make cloud-destroy' for full teardown."
            )
    print()


def cmd_on() -> None:
    check_prerequisites()
    status = check_live_status()
    instances = status["instances"]
    static_eip = status["static_eip"]

    if instances:
        inst = instances[0]
        inst_id = inst["id"]
        ip = static_eip or inst["public_ip"]

        if inst["state"] == "running":
            print_banner("Instance Already Running", "🟢")
            print(f"  • Instance ID: {inst_id}")
            print(f"  • Static IP:   {ip}")
            print(f"  • App URL:     http://{ip}")
            print("  • Domain URL:  https://openeval.studio")
            wait_for_health(f"http://{ip}/api/health", max_attempts=5)
            return

        if inst["state"] == "stopped":
            print_banner(f"Waking OpenEval Cloud Server from Sleep ({AWS_REGION})", "⚡")
            print(f"  • Instance ID: {inst_id}")
            print(f"  • Static IP:   {ip} (Preserved, no DNS change)")
            print("\nResuming EC2 instance via AWS CLI (~10-15 seconds)...\n")

            run_aws_json(["ec2", "start-instances", "--instance-ids", inst_id])

            print("Waiting for instance to reach running state...", end=" ", flush=True)
            for _ in range(15):
                st = run_aws_json(["ec2", "describe-instances", "--instance-ids", inst_id])
                state = (
                    st.get("Reservations", [{}])[0]
                    .get("Instances", [{}])[0]
                    .get("State", {})
                    .get("Name")
                )
                if state == "running":
                    print("🟢 Running!")
                    break
                time.sleep(2)
                print(".", end="", flush=True)

            wait_for_health(f"http://{ip}/api/health", max_attempts=15)

            print("\n" + "=" * 64)
            print(" 🎉 Instance is Awake and Live!")
            print(f"  🌐 Application URL:  http://{ip}")
            print("  🌐 Custom Domain:    https://openeval.studio")
            print(f"  🖥️  Static IP:        {ip}")
            print(f"  🔑 SSH Access:       ssh ubuntu@{ip}")
            print("=" * 64 + "\n")
            return

    # No instance exists: Full Terraform provision
    print_banner(f"Provisioning OpenEval Cloud Resources ({AWS_REGION})", "🚀")
    print("This provisions:")
    print("  • 1x London EC2 Instance (t3.micro, amd64, 2GB swap)")
    print("  • 1x 10GB gp3 SSD root volume with Docker PostgreSQL 16")
    print("  • 1x Static Elastic IP address (never changes)")
    print("  • 1x Prebuilt GHCR Docker Container")
    print("\nStarting Terraform apply (~2-3 minutes)...\n")

    cmd = ["terraform", f"-chdir={TF_DIR}", "apply", "-auto-approve"]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n❌ Terraform apply failed with code {exc.returncode}")
        sys.exit(exc.returncode)

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

    print_banner("Cloud Infrastructure Provisioned!", "🎉")
    print(f"  🌐 Application URL:  {public_url}")
    print("  🌐 Custom Domain:    https://openeval.studio")
    print(f"  🖥️  Static IP:        {public_ip}")
    print(f"  🔑 SSH Access:       {ssh_cmd}")

    wait_for_health(health_url, max_attempts=30)


def cmd_off() -> None:
    """Fast sleep mode: stops EC2 instance to eliminate compute billing ($0.00/hr)."""
    check_prerequisites()
    status = check_live_status()
    instances = status["instances"]
    static_eip = status["static_eip"]

    if not instances:
        print("ℹ️  No active EC2 instance found. Nothing to turn off.")
        return

    inst = instances[0]
    inst_id = inst["id"]
    ip = static_eip or inst["public_ip"]

    if inst["state"] == "stopped":
        print(f"ℹ️  Instance {inst_id} is already stopped (sleep mode). Compute is $0.00/hour.")
        return

    print_banner("Putting OpenEval Cloud Server to Sleep", "💤")
    print(f"Stopping instance {inst_id}...")
    print("  • EC2 compute billing drops to $0.00 / hour immediately.")
    print(f"  • Static IP {ip} is preserved permanently.")
    print("  • Database data, Docker images, and SSL certificates remain intact on disk.")
    print("-" * 64)

    run_aws_json(["ec2", "stop-instances", "--instance-ids", inst_id])

    print("Waiting for instance to stop...", end=" ", flush=True)
    for _ in range(20):
        st = run_aws_json(["ec2", "describe-instances", "--instance-ids", inst_id])
        state = (
            st.get("Reservations", [{}])[0].get("Instances", [{}])[0].get("State", {}).get("Name")
        )
        if state == "stopped":
            print("🟡 Stopped!")
            break
        time.sleep(2)
        print(".", end="", flush=True)

    print("\n" + "=" * 64)
    print(" 💤 Server is Asleep!")
    print("  • Compute cost:        $0.00 / hour")
    print(f"  • Preserved Static IP: {ip} (No DNS changes needed)")
    print("  • Holding cost:        ~$0.15 / day (Elastic IP + SSD volume)")
    print("\n 💡 When you want to use it again, run:")
    print("    make cloud-on         (Wakes up in ~15 seconds!)")
    print("\n 💡 If you ever want to completely delete everything ($0.00/month):")
    print("    make cloud-destroy")
    print("=" * 64 + "\n")


def cmd_destroy(auto_approve: bool = False) -> None:
    """Full teardown: runs terraform destroy to remove all resources including EIP."""
    check_prerequisites()
    print_banner("Destroying All OpenEval Cloud Resources", "🛑")
    print("This will completely destroy all AWS resources in London:")
    print("  • EC2 Instance (t3.micro)")
    print("  • Static Elastic IP address (will be released)")
    print("  • 10GB gp3 EBS root volume & PostgreSQL data")
    print("  • Dedicated VPC, subnets, and security groups")
    print("\nResult: 100% clean slate. Exactly $0.00 / month.")
    print("-" * 64)

    if not auto_approve:
        confirm = input("Are you sure you want to destroy everything? (y/N): ").strip().lower()
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
    print("  • All billable AWS resources have been cleanly destroyed.")
    print("  • Hourly burn rate: $0.00 / hour ($0.00 / month).")
    print("\nTo rebuild from scratch at any time, run: make cloud-on\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenEval Cloud Power Switch - Turn on/off AWS infrastructure with Static Elastic IP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # on / up / wake
    subparsers.add_parser(
        "on",
        aliases=["up", "wake", "start"],
        help="Wake up EC2 instance (~15s) or provision via Terraform",
    )

    # off / sleep / stop
    subparsers.add_parser(
        "off",
        aliases=["down", "sleep", "stop"],
        help="Put EC2 to sleep ($0.00/hr compute, preserves static IP & database)",
    )

    # destroy
    destroy_p = subparsers.add_parser(
        "destroy",
        help="Completely destroy all cloud resources via Terraform ($0.00/mo clean slate)",
    )
    destroy_p.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt and destroy immediately"
    )

    # status
    subparsers.add_parser("status", help="Inspect live AWS resources, static IP, & burn rate")

    args = parser.parse_args()

    if args.command in ("on", "up", "wake", "start"):
        cmd_on()
    elif args.command in ("off", "down", "sleep", "stop"):
        cmd_off()
    elif args.command == "destroy":
        cmd_destroy(auto_approve=getattr(args, "yes", False))
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
