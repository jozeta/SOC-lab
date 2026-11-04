#!/usr/bin/env python3
"""Wazuh integration: send an alert to DFIR-IRIS as an IRIS alert.

Installed on siem as /var/ossec/integrations/custom-iris by deploy-soc-lab.sh,
with an <integration> block (level 10+, alert_format json). Wazuh runs it as
    custom-iris <alert file> <api key> <hook url>
where the hook URL is https://<iris>/alerts/add. Standard library only.
IRIS' certificate is self-signed (made on the iris VM for its IP); the deploy
copies it to CA_FILE, and the connection is verified against it.
"""
import json
import ssl
import sys
import urllib.request

LOG = "/var/ossec/logs/integrations.log"
CA_FILE = "/var/ossec/etc/iris-cert.pem"


def severity(level):
    """Wazuh rule level -> IRIS severity id (4 Medium, 5 High, 6 Critical)."""
    if level >= 14:
        return 6
    if level >= 12:
        return 5
    return 4


def to_iris(alert):
    rule = alert.get("rule", {})
    agent = alert.get("agent", {})
    level = int(rule.get("level", 0))
    mitre = rule.get("mitre", {}).get("id", [])
    tags = ["wazuh", f"rule-{rule.get('id', '?')}", agent.get("name", "")] + mitre
    return {
        "alert_title": f"[{agent.get('name', '?')}] {rule.get('description', 'Wazuh alert')}"[:240],
        "alert_description": (f"Wazuh rule {rule.get('id')} (level {level}) on {agent.get('name')} "
                              f"({agent.get('ip', '-')})\n\n{alert.get('full_log', '')}")[:4000],
        "alert_source": "Wazuh",
        "alert_source_ref": alert.get("id", ""),
        "alert_source_event_time": alert.get("timestamp", ""),
        "alert_severity_id": severity(level),
        "alert_status_id": 2,                     # New
        "alert_customer_id": 1,                   # IRIS' initial customer
        "alert_tags": ",".join(t for t in tags if t),
        "alert_note": "",
        "alert_source_content": alert,
    }


def main():
    alert_file, api_key, hook = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(alert_file) as fh:
        alert = json.load(fh)
    req = urllib.request.Request(hook, data=json.dumps(to_iris(alert)).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json"})
    ctx = ssl.create_default_context(cafile=CA_FILE)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = f"{e.code} {e.read()[:200]!r}"
    except OSError as e:
        status = f"error {e}"
    with open(LOG, "a") as fh:
        fh.write(f"custom-iris: rule {alert.get('rule', {}).get('id')} alert {alert.get('id')}: {status}\n")


if __name__ == "__main__":
    main()
