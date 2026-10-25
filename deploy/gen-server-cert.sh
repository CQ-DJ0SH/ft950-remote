#!/usr/bin/env bash
# Issue the HTTPS server certificate for FT-950 Remote from an existing private
# Root CA — by default the one TM-V71 Remote created. Devices that already trust
# that CA (phones, browsers) then trust this server too, nothing to re-install.
# The CA key is only read, never copied. The leaf is valid 825 days (iOS limit).
#
#   deploy/gen-server-cert.sh [CA_DIR]      CA_DIR holds ca.crt + ca.key
set -euo pipefail
CA_DIR=${1:-/opt/tmv71-remote/backend/certs/ca}
SERVER_IP=${SERVER_IP:-$(hostname -I | awk '{print $1}')}   # this Pi's LAN address
OUT=$(cd "$(dirname "$0")/../backend" && pwd)/certs
[ -f "$CA_DIR/ca.key" ] || { echo "$CA_DIR/ca.key missing — cannot sign"; exit 1; }
mkdir -p "$OUT"; cd "$OUT"

cat > leaf.ext <<EXT
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
authorityKeyIdentifier = keyid,issuer
subjectKeyIdentifier = hash
subjectAltName = @alt
[alt]
DNS.1 = ft950.dj0sh.de
DNS.2 = raspberrypi
DNS.3 = raspberrypi.local
DNS.4 = localhost
IP.1 = ${SERVER_IP}
IP.2 = 127.0.0.1
EXT

[ -f cert.pem ] && cp -f cert.pem cert.pem.bak && cp -f key.pem key.pem.bak
openssl genrsa -out key.pem 2048 2>/dev/null
openssl req -new -key key.pem -subj "/CN=ft950.dj0sh.de/O=FT-950 Remote" -out server.csr
# Random 128-bit serial, never a counter: copies of this CA live on several
# Pis, each with its own ca.srl. Counters collided (TM-V71 Remote on the other Pi and
# this certificate both got ...45DC), and Firefox refuses a second certificate
# with the same issuer + serial (SEC_ERROR_REUSED_ISSUER_AND_SERIAL).
openssl x509 -req -in server.csr -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" \
  -set_serial "0x$(openssl rand -hex 16)" -days 825 -sha256 -extfile leaf.ext \
  -out cert.pem 2>/dev/null
chmod 600 key.pem
rm -f server.csr
cp -f "$CA_DIR/ca.crt" ca.crt             # public part only, offered for download
openssl verify -CAfile ca.crt cert.pem
