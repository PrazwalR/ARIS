#!/usr/bin/env bash
# Grant per-bank Kafka ACLs on risk-signals (docs/SECURITY.md SS3.8).
#
# Run against the PLAINTEXT listener (mapped to User:ANONYMOUS, a super user
# -- see docker-compose.yml) so this doesn't itself need a client cert; the
# ACLs it grants apply broker-wide, including to the SSL_HOST listener every
# bank actually connects over.
#
#   scripts/setup_kafka_acls.sh [BANK_ID ...]
#
# Defaults to BANK-A and BANK-B (not BANK-EVIL -- its absence is the point:
# tests/test_kafka_mtls.py connects as BANK-EVIL over mTLS and confirms the
# broker authenticates it fine but the authorizer still refuses its writes).

set -euo pipefail

BOOTSTRAP="localhost:9092"
TOPIC="risk-signals"
if [ "$#" -gt 0 ]; then
    BANKS=("$@")
else
    BANKS=("BANK-A" "BANK-B")
fi

run_acls() {
    docker exec aris-kafka /opt/kafka/bin/kafka-acls.sh --bootstrap-server "$BOOTSTRAP" "$@"
}

for bank in "${BANKS[@]}"; do
    echo "granting Write+Describe on ${TOPIC} to User:${bank}"
    run_acls --add --allow-principal "User:${bank}" --operation Write --operation Describe --topic "$TOPIC"
done

echo "granting Read+Describe on ${TOPIC} to every bank (consuming the compacted topic to build a local view is not a per-bank-scoped operation)"
# No --group: aris.kafka_bus.KafkaRiskBus uses group_id=None (manual partition
# assignment, not a consumer group -- see aris/kafka_bus.py), so there is no
# group resource for a group ACL to apply to here.
run_acls --add --allow-principal "User:*" --operation Read --operation Describe --topic "$TOPIC"

echo
echo "current ACLs on ${TOPIC}:"
run_acls --list --topic "$TOPIC"
