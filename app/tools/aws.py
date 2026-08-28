"""
AWS EC2 monitoring. Read-only (describe_* calls only) — never creates,
terminates, stops, starts, or modifies anything. Uses boto3's normal
credential resolution (env vars, ~/.aws/credentials, instance role) —
no custom credential handling, nothing hardcoded, and credentials are
never exposed in tool output or logs.

If AWS isn't configured, returns a clear error instead of crashing —
boto3 raises on first real API call when there are no credentials, not
at import time, so that's caught per-call here.
"""
from app.config import Config

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def _client(service: str):
    kwargs = {"region_name": Config.AWS_REGION} if Config.AWS_REGION else {}
    return boto3.client(service, **kwargs)


def list_ec2_instances() -> dict:
    """List EC2 instances with id, name, state, type, AZ, and IPs."""
    if not _BOTO3_AVAILABLE:
        return {"success": False, "error": "boto3 isn't installed. Run: pip install boto3"}

    try:
        ec2 = _client("ec2")
        response = ec2.describe_instances()
    except NoCredentialsError:
        return {"success": False, "error": "AWS credentials aren't configured. Set them via env vars or ~/.aws/credentials."}
    except (ClientError, BotoCoreError) as e:
        msg = _safe_error(e)
        if "must specify a region" in msg.lower():
            return {"success": False, "error": "AWS region isn't configured. Set AWS_DEFAULT_REGION in .env."}
        return {"success": False, "error": f"AWS API error: {msg}"}

    instances = []
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), None)
            instances.append({
                "instance_id": inst.get("InstanceId"),
                "name": name,
                "state": inst.get("State", {}).get("Name"),
                "instance_type": inst.get("InstanceType"),
                "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
                "private_ip": inst.get("PrivateIpAddress"),
                "public_ip": inst.get("PublicIpAddress"),
            })

    return {"success": True, "instances": instances, "count": len(instances)}


def get_ec2_cpu_metrics(instance_id: str, minutes: int = 30) -> dict:
    """Average CPU utilization for an instance over the last N minutes (CloudWatch)."""
    if not _BOTO3_AVAILABLE:
        return {"success": False, "error": "boto3 isn't installed. Run: pip install boto3"}
    if not instance_id or not isinstance(instance_id, str):
        return {"success": False, "error": "instance_id is required."}

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 30
    minutes = max(5, min(minutes, 1440))

    from datetime import datetime, timedelta, timezone
    try:
        cw = _client("cloudwatch")
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        response = cw.get_metric_statistics(
            Namespace="AWS/EC2", MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start, EndTime=end, Period=300, Statistics=["Average"],
        )
    except NoCredentialsError:
        return {"success": False, "error": "AWS credentials aren't configured."}
    except (ClientError, BotoCoreError) as e:
        return {"success": False, "error": f"AWS API error: {_safe_error(e)}"}

    points = sorted(response.get("Datapoints", []), key=lambda p: p["Timestamp"])
    if not points:
        return {"success": True, "instance_id": instance_id, "average_cpu_percent": None, "detail": "No datapoints in that window."}

    avg = sum(p["Average"] for p in points) / len(points)
    return {"success": True, "instance_id": instance_id, "average_cpu_percent": round(avg, 1), "datapoints": len(points)}


def _safe_error(e) -> str:
    """Strip anything resembling a credential/account ID from an AWS error before it's shown."""
    msg = str(e)
    return msg.split("Encoded authorization failure")[0][:300]
