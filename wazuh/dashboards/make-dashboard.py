#!/usr/bin/env python3
"""Generate soc-lab.ndjson: the SOC-lab dashboard and saved searches for the
Wazuh dashboard (OpenSearch Dashboards saved objects).

    python3 wazuh/dashboards/make-dashboard.py > wazuh/dashboards/soc-lab.ndjson

The objects reference the index pattern "wazuh-alerts-*"; install-dashboards.py
creates it (with its field list) if the Wazuh app hasn't yet.
"""
import json

INDEX = "wazuh-alerts-*"
REF = "kibanaSavedObjectMeta.searchSourceJSON.index"


def source(query):
    return json.dumps({"query": {"query": query, "language": "kuery"}, "filter": [], "indexRefName": REF})


def vis(vid, title, vtype, aggs, params, query=""):
    return {"type": "visualization", "id": vid, "references": [{"name": REF, "type": "index-pattern", "id": INDEX}],
            "attributes": {"title": title, "description": "", "version": 1, "uiStateJSON": "{}",
                           "visState": json.dumps({"title": title, "type": vtype, "aggs": aggs, "params": params}),
                           "kibanaSavedObjectMeta": {"searchSourceJSON": source(query)}}}


def count():
    return {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}}


def terms(field, size=10, schema="bucket", agg_id="2"):
    return {"id": agg_id, "enabled": True, "type": "terms", "schema": schema,
            "params": {"field": field, "size": size, "order": "desc", "orderBy": "1",
                       "otherBucket": False, "missingBucket": False}}


def metric(vid, title, query):
    return vis(vid, title, "metric", [count()], {
        "addTooltip": True, "addLegend": False, "type": "metric",
        "metric": {"percentageMode": False, "useRanges": False, "colorSchema": "Green to Red",
                   "metricColorMode": "None", "colorsRange": [{"from": 0, "to": 10000}],
                   "labels": {"show": True}, "invertColors": False,
                   "style": {"bgFill": "#000", "bgColor": False, "labelColor": False, "subText": "", "fontSize": 48}}},
        query)


def table(vid, title, field, query, size=10):
    return vis(vid, title, "table", [count(), terms(field, size)], {
        "perPage": 10, "showPartialRows": False, "showMetricsAtAllLevels": False, "showTotal": False,
        "totalFunc": "sum", "percentageCol": ""}, query)


def pie(vid, title, field, query):
    return vis(vid, title, "pie", [count(), terms(field, 10, "segment")], {
        "type": "pie", "addTooltip": True, "addLegend": True, "legendPosition": "right", "isDonut": True,
        "labels": {"show": False, "values": True, "last_level": True, "truncate": 100}}, query)


def timeline(vid, title, split_field, query=""):
    aggs = [count(),
            {"id": "2", "enabled": True, "type": "date_histogram", "schema": "segment",
             "params": {"field": "timestamp", "useNormalizedOpenSearchInterval": True, "interval": "auto",
                        "drop_partials": False, "min_doc_count": 1, "extended_bounds": {}}},
            terms(split_field, 8, "group", "3")]
    params = {
        "type": "histogram", "grid": {"categoryLines": False}, "addTooltip": True, "addLegend": True,
        "legendPosition": "right", "times": [], "addTimeMarker": False,
        "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom", "show": True,
                          "style": {}, "scale": {"type": "linear"}, "labels": {"show": True, "filter": True,
                                                                                "truncate": 100}, "title": {}}],
        "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value", "position": "left", "show": True,
                       "style": {}, "scale": {"type": "linear", "mode": "normal"},
                       "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                       "title": {"text": "Alerts"}}],
        "seriesParams": [{"show": True, "type": "histogram", "mode": "stacked", "data": {"label": "Alerts", "id": "1"},
                          "valueAxis": "ValueAxis-1", "drawLinesBetweenPoints": True, "showCircles": True}]}
    return vis(vid, title, "histogram", aggs, params, query)


def search(sid, title, query, columns):
    return {"type": "search", "id": sid, "references": [{"name": REF, "type": "index-pattern", "id": INDEX}],
            "attributes": {"title": title, "description": "", "hits": 0, "version": 1, "columns": columns,
                           "sort": [["timestamp", "desc"]],
                           "kibanaSavedObjectMeta": {"searchSourceJSON": source(query)}}}


ATTACK = "rule.level >= 10"
AUTH_FAIL = "rule.groups:authentication_failed"
PROXY = "rule.id:100190"
WEBAPP = "rule.id:(100160 or 100161 or 100162 or 100170 or 100171 or 100172 or 100173 or 100180 or 100181)"
CICD = "rule.groups:cicd"
MAIL = "rule.groups:(postfix or dovecot or roundcube or mail)"

panels = [  # (object, x, y, w, h)
    (metric("soclab-m-attacks", "Attack alerts (level 10+)", ATTACK), 0, 0, 12, 7),
    (metric("soclab-m-authfail", "Failed logins", AUTH_FAIL), 12, 0, 12, 7),
    (metric("soclab-m-honeypot", "Honeypot touches", "rule.groups:deception"), 24, 0, 12, 7),
    (metric("soclab-m-proxy", "Proxied web requests", PROXY), 36, 0, 12, 7),
    (timeline("soclab-t-alerts", "Alerts over time, by VM", "agent.name"), 0, 7, 48, 13),
    (table("soclab-tb-attacks", "Attack rules that fired", "rule.description", ATTACK), 0, 20, 24, 14),
    (pie("soclab-p-authfail", "Failed logins by VM", "agent.name", AUTH_FAIL), 24, 20, 24, 14),
    (table("soclab-tb-proxy-clients", "Proxy: clients", "data.srcip", PROXY), 0, 34, 16, 14),
    (table("soclab-tb-proxy-urls", "Proxy: top URLs", "data.url", PROXY, 15), 16, 34, 32, 14),
    (table("soclab-tb-webapps", "Web-app logins and jobs", "rule.description", WEBAPP), 0, 48, 24, 14),
    (table("soclab-tb-cicd", "CI/CD events", "rule.description", CICD), 24, 48, 24, 14),
    (table("soclab-tb-mail", "Mail events", "rule.description", MAIL), 0, 62, 48, 12),
]
searches = [
    search("soclab-s-attacks", "SOC-lab: attacks (level 10+)", ATTACK,
           ["agent.name", "rule.id", "rule.description", "data.srcip"]),
    search("soclab-s-authfail", "SOC-lab: failed logins", AUTH_FAIL, ["agent.name", "rule.description"]),
    search("soclab-s-proxy", "SOC-lab: proxy log", PROXY, ["data.srcip", "data.action", "data.url"]),
    search("soclab-s-mail", "SOC-lab: mail", MAIL, ["agent.name", "rule.description"]),
]

refs, grid = [], []
for i, (obj, x, y, w, h) in enumerate(panels):
    refs.append({"name": f"panel_{i}", "type": "visualization", "id": obj["id"]})
    grid.append({"panelIndex": str(i + 1), "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(i + 1)},
                 "embeddableConfig": {}, "version": "2.19.0", "panelRefName": f"panel_{i}"})
dashboard = {"type": "dashboard", "id": "soclab-overview", "references": refs,
             "attributes": {"title": "SOC-lab overview", "hits": 0, "version": 1,
                            "description": "Attacks, logins, proxy, web apps, CI/CD and mail across the lab",
                            "panelsJSON": json.dumps(grid), "optionsJSON": json.dumps({"hidePanelTitles": False,
                                                                                         "useMargins": True}),
                            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now", "refreshInterval":
                            {"pause": False, "value": 60000},
                            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                                {"query": {"query": "", "language": "kuery"}, "filter": []})}}}

for obj in [p[0] for p in panels] + searches + [dashboard]:
    print(json.dumps(obj, separators=(",", ":")))
