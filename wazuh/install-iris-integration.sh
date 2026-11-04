#!/bin/bash
# Run on siem by deploy-soc-lab.sh (install_iris_integration), as root:
#   install-iris-integration.sh <iris ip>     (API key on stdin)
# Installs the custom-iris integration (Wazuh alerts of level 10+ -> DFIR-IRIS)
# from the files the deploy copied to /tmp, and (re)writes its <integration>
# block in ossec.conf. Safe to re-run.
set -euo pipefail
iris_ip="$1"
key=$(cat)
[[ "${iris_ip}" =~ ^[0-9.]+$ ]] || { echo "bad IRIS address: ${iris_ip}"; exit 1; }
[[ "${key}" =~ ^[0-9a-f]{64}$ ]] || { echo "bad IRIS API key"; exit 1; }

install -o root -g wazuh -m 750 /tmp/custom-iris.py /var/ossec/integrations/custom-iris
install -o root -g wazuh -m 640 /tmp/iris-cert.pem /var/ossec/etc/iris-cert.pem
rm -f /tmp/custom-iris.py /tmp/iris-cert.pem

conf=/var/ossec/etc/ossec.conf
begin="<!-- SOC-lab: DFIR-IRIS integration (begin) -->"
end="<!-- SOC-lab: DFIR-IRIS integration (end) -->"
sed -i "/${begin}/,/${end}/d" "${conf}"
cat >> "${conf}" <<EOF
${begin}
<ossec_config>
  <integration>
    <name>custom-iris</name>
    <hook_url>https://${iris_ip}/alerts/add</hook_url>
    <api_key>${key}</api_key>
    <level>10</level>
    <alert_format>json</alert_format>
  </integration>
</ossec_config>
${end}
EOF
systemctl restart wazuh-manager
