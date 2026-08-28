"""
Tests for app/tools/aws.py. All boto3 calls are mocked — no real AWS API
is ever contacted.
"""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from app.tools import aws as aws_tools


def test_list_ec2_instances_success():
    fake_response = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-123",
                "State": {"Name": "running"},
                "InstanceType": "t3.micro",
                "Placement": {"AvailabilityZone": "us-east-1a"},
                "PrivateIpAddress": "10.0.0.1",
                "PublicIpAddress": "1.2.3.4",
                "Tags": [{"Key": "Name", "Value": "web-server"}],
            }]
        }]
    }
    mock_client = MagicMock()
    mock_client.describe_instances.return_value = fake_response
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.list_ec2_instances()

    assert result["success"] is True
    assert result["count"] == 1
    assert result["instances"][0]["name"] == "web-server"
    assert result["instances"][0]["state"] == "running"


def test_list_ec2_instances_no_credentials():
    from botocore.exceptions import NoCredentialsError
    mock_client = MagicMock()
    mock_client.describe_instances.side_effect = NoCredentialsError()
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.list_ec2_instances()

    assert result["success"] is False
    assert "credentials" in result["error"].lower()


def test_list_ec2_instances_client_error():
    from botocore.exceptions import ClientError
    mock_client = MagicMock()
    mock_client.describe_instances.side_effect = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "not allowed"}}, "DescribeInstances"
    )
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.list_ec2_instances()

    assert result["success"] is False
    assert "AWS API error" in result["error"]


def test_list_ec2_instances_boto3_not_installed():
    with patch("app.tools.aws._BOTO3_AVAILABLE", False):
        result = aws_tools.list_ec2_instances()
    assert result["success"] is False
    assert "boto3" in result["error"].lower()


def test_list_ec2_instances_missing_region_gives_clear_message():
    from botocore.exceptions import BotoCoreError

    class FakeNoRegionError(BotoCoreError):
        fmt = "You must specify a region."

    mock_client = MagicMock()
    mock_client.describe_instances.side_effect = FakeNoRegionError()
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.list_ec2_instances()

    assert result["success"] is False
    assert "region" in result["error"].lower()


def test_get_ec2_cpu_metrics_missing_instance_id():
    result = aws_tools.get_ec2_cpu_metrics(None)
    assert result["success"] is False


def test_get_ec2_cpu_metrics_success():
    fake_response = {
        "Datapoints": [
            {"Average": 10.0, "Timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)},
            {"Average": 20.0, "Timestamp": datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)},
        ]
    }
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = fake_response
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.get_ec2_cpu_metrics("i-123", 30)

    assert result["success"] is True
    assert result["average_cpu_percent"] == 15.0


def test_get_ec2_cpu_metrics_no_datapoints():
    mock_client = MagicMock()
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    with patch("app.tools.aws.boto3.client", return_value=mock_client), \
         patch("app.tools.aws._BOTO3_AVAILABLE", True):
        result = aws_tools.get_ec2_cpu_metrics("i-123")

    assert result["success"] is True
    assert result["average_cpu_percent"] is None
