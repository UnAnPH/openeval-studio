#!/usr/bin/env python3
"""OpenEval Studio - Cloud Power Switch.

Provides an easy, one-command mechanism to turn on and turn off all AWS cloud resources
to prevent unexpected cloud credit consumption.

Zero external Python dependencies required (uses built-in standard library + aws cli / terraform).

Usage:
  python3 scripts/cloud_switch.py on      # Spin up all resources via Terraform (~3-4 min)
  python3 scripts/cloud_switch.py off     # Destroy all resources for $0.00/hr clean slate
  python3 scripts/cloud_switch.py status  # Inspect live AWS resources & hourly burn rate
  python3 scripts/cloud_switch.py pause   # Fast-stop EC2 & RDS (ALB still incurs base charges)
  python3 scripts/cloud_switch.py resume  # Fast-start EC2 & RDS
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
COST_ALB_HOUR = 0.0225  # ~$16.40/month
COST_EC2_T3_MICRO_HOUR = 0.0104  # ~$7.50/month (Free Tier eligible: 750 hrs/mo)
COST_RDS_T4G_MICRO_HOUR = 0.0160  # ~$11.60/month (Free Tier eligible: 750 hrs/mo)
COST_PUBLIC_IPV4_HOUR = 0.0050  # ~$3.60/month
COST_EBS_GP3_GB_MONTH = 0.08  # ~$2.40/month for 30GB


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
    """Inspect live AWS resources in the target region."""
    # 1. EC2 Instances
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
            if inst.get("State", {}).get("Name") != "terminated":
                instances.append(
                    {
                        "id": inst["InstanceId"],
                        "type": inst.get("InstanceType"),
                        "state": inst.get("State", {}).get("Name"),
                        "public_ip": inst.get("PublicIpAddress", "none"),
                    }
                )

    # 2. RDS Instances
    rds_data = run_aws_json(["rds", "describe-db-instances"])
    databases: list[dict[str, Any]] = []
    for db in rds_data.get("DBInstances", []):
        db_id = db.get("DBInstanceIdentifier", "")
        if "openeval" in db_id.lower():
            databases.append(
                {
                    "id": db_id,
                    "class": db.get("DBInstanceClass"),
                    "status": db.get("DBInstanceStatus"),
                    "endpoint": db.get("Endpoint", {}).get("Address", "none"),
                }
            )

    # 3. Load Balancers
    elb_data = run_aws_json(["elbv2", "describe-load-balancers"])
    load_balancers: list[dict[str, Any]] = []
    for elb in elb_data.get("LoadBalancers", []):
        name = elb.get("LoadBalancerName", "")
        if "openeval" in name.lower():
            load_balancers.append(
                {
                    "name": name,
                    "dns": elb.get("DNSName"),
                    "state": elb.get("State", {}).get("Code"),
                }
            )

    # Compute estimated hourly burn rate
    hourly_rate = 0.0
    for inst in instances:
        if inst["state"] == "running":
            hourly_rate += COST_EC2_T3_MICRO_HOUR + COST_PUBLIC_IPV4_HOUR
    for db in databases:
        if db["status"] == "available":
            hourly_rate += COST_RDS_T4G_MICRO_HOUR
    for elb in load_balancers:
        if elb["state"] == "active":
            hourly_rate += COST_ALB_HOUR

    return {
        "region": AWS_REGION,
        "instances": instances,
        "databases": databases,
        "load_balancers": load_balancers,
        "hourly_rate": hourly_rate,
    }


def cmd_status() -> None:
    check_prerequisites()
    print_banner(f"OpenEval Cloud Status ({AWS_REGION})", "🔎")
    status = check_live_status()

    instances = status["instances"]
    databases = status["databases"]
    elbs = status["load_balancers"]
    rate = status["hourly_rate"]

    print(f"Region:             {status['region']}")
    print(f"Active Resources:   {len(instances)} EC2, {len(databases)} RDS, {len(elbs)} ALB")
    print("-" * 64)

    # EC2
    if not instances:
        print("  EC2 Instances:    None active (0 instances)")
    else:
        for inst in instances:
            state_emoji = "🟢" if inst["state"] == "running" else "🟡"
            print(
                f"  EC2 Instance:     {state_emoji} {inst['id']} ({inst['type']}) -> {inst['state']} (IP: {inst['public_ip']})"
            )

    # RDS
    if not databases:
        print("  RDS Databases:    None active (0 databases)")
    else:
        for db in databases:
            db_emoji = "🟢" if db["status"] == "available" else "🟡"
            print(
                f"  RDS Database:     {db_emoji} {db['id']} ({db['class']}) -> {db['status']} (Host: {db['endpoint']})"
            )

    # ALB
    if not elbs:
        print("  Load Balancers:   None active (0 ALBs)")
    else:
        for elb in elbs:
            elb_emoji = "🟢" if elb["state"] == "active" else "🟡"
            print(f"  Load Balancer:    {elb_emoji} {elb['name']} -> {elb['state']} ({elb['dns']})")

    print("-" * 64)
    if rate == 0.0:
        print("💰 Estimated Cloud Burn Rate:  $0.00 / hour ($0.00 / month)")
        print("   ✅ All billable cloud resources are fully OFF. Zero credits being spent.")
    else:
        est_monthly = rate * 24 * 30.5
        print(f"💰 Estimated Cloud Burn Rate:  ~${rate:.4f} / hour (~${est_monthly:.2f} / month)")
        if any(elbs):
            print(
                "   ⚠️  NOTE: ALB charges ~$0.0225/hr regardless of traffic. Run 'off' for $0.00/hr teardown."
            )
    print()


def cmd_on() -> None:
    check_prerequisites()
    print_banner(f"Turning ON OpenEval Cloud Resources ({AWS_REGION})", "🚀")
    print("This provisions:")
    print("  • 1x Application Load Balancer (ALB)")
    print("  • 1x EC2 Instance (t3.micro, Ubuntu 24.04, 3GB swap)")
    print("  • 1x RDS PostgreSQL Instance (db.t4g.micro)")
    print("  • Dedicated VPC, subnets, and security groups")
    print("\nStarting Terraform apply (~3-4 minutes)...\n")

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
        alb_url = outputs.get("alb_preview_url", {}).get("value", "")
        ec2_ip = outputs.get("ec2_public_ip", {}).get("value", "")
        ssh_cmd = outputs.get("ec2_ssh_command", {}).get("value", "")
    except Exception:
        alb_url, ec2_ip, ssh_cmd = "", "", ""

    print_banner("Cloud Infrastructure is Provisioned!", "🎉")
    if alb_url:
        print(f"  🌐 Application URL:  {alb_url}")
    if ec2_ip:
        print(f"  🖥️  EC2 Public IP:    {ec2_ip}")
    if ssh_cmd:
        print(f"  🔑 SSH Access:       {ssh_cmd}")

    print("\nWaiting for web service to become healthy (cloud-init bootstrapping)...")
    health_url = f"{alb_url}/api/health" if alb_url else ""
    healthy = False
    if health_url:
        for attempt in range(1, 25):
            print(f"  [Probe {attempt}/24] Checking {health_url} ...", end=" ", flush=True)
            try:
                req = urllib.request.Request(health_url, headers={"User-Agent": "CloudSwitch/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        print("✅ Healthy!")
                        healthy = True
                        break
            except Exception:
                print("⏳ starting up...")
                time.sleep(10)

    if not healthy and alb_url:
        print("  ℹ️  EC2 instance is pulling Docker images. Give it ~60 seconds to complete.")

    print("\n" + "=" * 64)
    print(" 💡 To test the remote Policy Gateway:")
    print(f"    python3 scripts/test_cloud_watcher.py {alb_url}")
    print("\n 💡 When you are finished, turn everything OFF to save credits:")
    print("    python3 scripts/cloud_switch.py off    (or: make cloud-off)")
    print("=" * 64 + "\n")


def cmd_off(auto_approve: bool = False) -> None:
    check_prerequisites()
    print_banner("Turning OFF OpenEval Cloud Resources", "🛑")
    print("This will destroy all AWS resources in London:")
    print("  • Application Load Balancer ($16.40/mo saved)")
    print("  • EC2 Instance & 30GB gp3 EBS volume ($9.90/mo saved)")
    print("  • RDS PostgreSQL database ($11.60/mo saved)")
    print("  • Public IPv4 address ($3.60/mo saved)")
    print("\nResult: Guaranteed $0.00 / hour burn rate. No credit leak.")
    print("-" * 64)

    if not auto_approve:
        confirm = (
            input("Are you sure you want to destroy all cloud resources? (y/N): ").strip().lower()
        )
        if confirm not in ("y", "yes"):
            print("Teardown cancelled.")
            return

    print("\nDestroying resources via Terraform (~2 minutes)...\n")
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
    print("\nTo turn it back on at any time, run: python3 scripts/cloud_switch.py on\n")


def cmd_pause() -> None:
    """Stops EC2 and RDS instances without destroying infrastructure."""
    check_prerequisites()
    print_banner("Pausing Compute & Database (Quick Sleep)", "⏸️")

    # Stop EC2
    ec2_data = run_aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            "Name=tag:Project,Values=OpenEval-Studio,openeval",
        ]
    )
    inst_ids = [
        inst["InstanceId"]
        for res in ec2_data.get("Reservations", [])
        for inst in res.get("Instances", [])
        if inst.get("State", {}).get("Name") == "running"
    ]
    if inst_ids:
        print(f"Stopping EC2 instance(s): {', '.join(inst_ids)} ...")
        run_aws_json(["ec2", "stop-instances", "--instance-ids"] + inst_ids)
        print("✅ EC2 stop signal sent.")
    else:
        print("No running EC2 instances found.")

    # Stop RDS
    rds_data = run_aws_json(["rds", "describe-db-instances"])
    for db in rds_data.get("DBInstances", []):
        db_id = db.get("DBInstanceIdentifier", "")
        if "openeval" in db_id.lower() and db.get("DBInstanceStatus") == "available":
            print(f"Stopping RDS instance: {db_id} ...")
            run_aws_json(["rds", "stop-db-instance", "--db-instance-identifier", db_id])
            print("✅ RDS stop signal sent.")

    print("\n" + "!" * 64)
    print(" ⚠️  IMPORTANT COST NOTICE:")
    print("    EC2 and RDS compute hours are paused.")
    print("    HOWEVER, the Application Load Balancer (ALB) and public IPv4 are STILL active")
    print("    and incur ~$0.0275/hr (~$0.66/day).")
    print("    For a guaranteed $0.00/hr cost, run: python3 scripts/cloud_switch.py off")
    print("!" * 64 + "\n")


def cmd_resume() -> None:
    """Resumes paused EC2 and RDS instances."""
    check_prerequisites()
    print_banner("Resuming Compute & Database", "▶️")

    ec2_data = run_aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            "Name=tag:Project,Values=OpenEval-Studio,openeval",
        ]
    )
    inst_ids = [
        inst["InstanceId"]
        for res in ec2_data.get("Reservations", [])
        for inst in res.get("Instances", [])
        if inst.get("State", {}).get("Name") == "stopped"
    ]
    if inst_ids:
        print(f"Starting EC2 instance(s): {', '.join(inst_ids)} ...")
        run_aws_json(["ec2", "start-instances", "--instance-ids"] + inst_ids)
        print("✅ EC2 start signal sent.")
    else:
        print("No stopped EC2 instances found.")

    rds_data = run_aws_json(["rds", "describe-db-instances"])
    for db in rds_data.get("DBInstances", []):
        db_id = db.get("DBInstanceIdentifier", "")
        if "openeval" in db_id.lower() and db.get("DBInstanceStatus") == "stopped":
            print(f"Starting RDS instance: {db_id} ...")
            run_aws_json(["rds", "start-db-instance", "--db-instance-identifier", db_id])
            print("✅ RDS start signal sent.")

    print("\nDone. Give services ~1-2 minutes to re-establish connectivity.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenEval Cloud Power Switch - Turn on/off AWS infrastructure to save credits"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # on / up
    subparsers.add_parser(
        "on", aliases=["up"], help="Spin up cloud infrastructure via Terraform (~3-4 min)"
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

    # pause
    subparsers.add_parser("pause", help="Fast-stop EC2 & RDS (ALB still incurs hourly charges)")

    # resume
    subparsers.add_parser("resume", help="Resume paused EC2 & RDS")

    args = parser.parse_args()

    if args.command in ("on", "up"):
        cmd_on()
    elif args.command in ("off", "down"):
        cmd_off(auto_approve=getattr(args, "yes", False))
    elif args.command == "status":
        cmd_status()
    elif args.command == "pause":
        cmd_pause()
    elif args.command == "resume":
        cmd_resume()


if __name__ == "__main__":
    main()
