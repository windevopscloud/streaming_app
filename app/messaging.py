"""Email (SES) and SMS (SNS) senders. MESSAGING=console records to the outbox table instead."""
import time
import uuid

from . import config
from .db import db

_ses = None
_sns = None


def _record(channel, to, subject, body):
    db.x(
        "INSERT INTO outbox (id, channel, recipient, subject, body, created) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, channel, to, subject, body, time.time_ns() // 1000),
    )
    print(f"[console-{channel}] to={to} subject={subject!r} body={body!r}", flush=True)


def send_email(to, subject, body):
    if config.MESSAGING != "aws":
        return _record("email", to, subject, body)
    global _ses
    if _ses is None:
        import boto3
        _ses = boto3.client("sesv2", region_name=config.AWS_REGION)
    _ses.send_email(
        FromEmailAddress=config.SES_FROM,
        Destination={"ToAddresses": [to]},
        Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
    )


def send_sms(to, body):
    if config.MESSAGING != "aws":
        return _record("sms", to, None, body)
    global _sns
    if _sns is None:
        import boto3
        _sns = boto3.client("sns", region_name=config.AWS_REGION)
    attrs = {"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}}
    if config.SNS_SENDER_ID:
        attrs["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": config.SNS_SENDER_ID}
    _sns.publish(PhoneNumber=to, Message=body, MessageAttributes=attrs)
