#!/usr/bin/env python3
"""Install the SOC-lab dashboard and saved searches into the Wazuh dashboard.

Run on siem by deploy-soc-lab.sh (install_dashboards), as root:
    install-dashboards.py <ndjson> https://127.0.0.1 /etc/wazuh-dashboard/certs/root-ca.pem
(admin password on stdin)

Creates the "wazuh-alerts-*" index pattern (with its field list, which this
OpenSearch Dashboards version stores in the saved object) when the Wazuh app
hasn't yet, then imports the objects, overwriting only the lab's own
(ids "soclab-*"). The TLS connection is verified against the given CA.
Standard library only. Safe to re-run.
"""
import base64
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

INDEX = "wazuh-alerts-*"


def main():
    ndjson, base, cafile = sys.argv[1], sys.argv[2].rstrip("/"), sys.argv[3]
    password = sys.stdin.read().strip()
    ctx = ssl.create_default_context(cafile=cafile)   # the dashboard's certificate is for IP 127.0.0.1
    auth = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()

    def call(method, path, body=None, ctype="application/json"):
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers={"Authorization": auth, "osd-xsrf": "true", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    st, _ = call("GET", "/api/saved_objects/index-pattern/" + urllib.parse.quote(INDEX, safe=""))
    if st == 404:
        q = urllib.parse.urlencode([("pattern", INDEX), ("meta_fields", "_source"), ("meta_fields", "_id"),
                                    ("meta_fields", "_index"), ("meta_fields", "_score")])
        st, fields = call("GET", f"/api/index_patterns/_fields_for_wildcard?{q}")
        if st != 200:
            sys.exit(f"could not read the {INDEX} fields ({st}) -- are there alerts yet?")
        st, _ = call("POST", "/api/saved_objects/index-pattern/" + urllib.parse.quote(INDEX, safe=""),
                     json.dumps({"attributes": {"title": INDEX, "timeFieldName": "timestamp",
                                                "fields": json.dumps(fields["fields"])}}).encode())
        print(f"index pattern {INDEX}: created ({st}, {len(fields['fields'])} fields)")
    else:
        print(f"index pattern {INDEX}: already there ({st})")

    boundary = uuid.uuid4().hex
    data = open(ndjson, "rb").read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"soc-lab.ndjson\"\r\n"
            f"Content-Type: application/ndjson\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    st, res = call("POST", "/api/saved_objects/_import?overwrite=true", body,
                   f"multipart/form-data; boundary={boundary}")
    if st != 200 or not res.get("success"):
        sys.exit(f"import failed ({st}): {json.dumps(res.get('errors', res))[:500]}")
    print(f"imported {res.get('successCount')} objects (dashboard: SOC-lab overview)")


if __name__ == "__main__":
    main()
