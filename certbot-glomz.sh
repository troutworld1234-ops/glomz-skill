#!/bin/bash
# Certbot for glomz.com
# Run this once DNS fully propagates globally
# Usage: ./certbot-glomz.sh

DOMAIN="glomz.com"
IP="72.60.167.129"

echo "Checking if glomz.com resolves to our IP from our server..."
resolve_ip=$(dig +short $DOMAIN | head -1)
if [ "$resolve_ip" = "$IP" ]; then
    echo "✅ DNS resolved to $IP"
else
    echo "❌ DNS resolved to $resolve_ip (expected $IP)"
    echo "Waiting... will retry in 30 seconds"
    for i in $(seq 1 20); do
        sleep 30
        resolve_ip=$(dig +short $DOMAIN | head -1)
        if [ "$resolve_ip" = "$IP" ]; then
            echo "✅ DNS resolved to $IP after attempt $i"
            break
        fi
        echo "Still waiting... ($resolve_ip)"
    done
fi

certbot certonly --nginx -d $DOMAIN -d www.$DOMAIN \
    --non-interactive --agree-tos --email jeff@glomz.com

if [ $? -eq 0 ]; then
    echo "✅ SSL cert issued!"
    echo "Update /etc/nginx/sites-available/glomz.com with the new cert paths"
else
    echo "❌ Failed to get SSL cert"
fi
