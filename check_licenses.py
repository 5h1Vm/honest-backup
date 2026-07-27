#!/usr/bin/env python3
import os
import sys

# Load .env file if present

def main():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip().strip('"\'')

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from lib.secrets import load_env
    from collectors.m365.graph import get_token
    import requests, json

    # Load env vars (KEEPASS_DATABASE, KEEPASS_PASSWORD) from .env via os.environ
    # Then load secrets from KeePass
    env = load_env()
    print("Loaded env vars from .env:", {k: env.get(k) for k in ['KEEPASS_DATABASE', 'KEEPASS_PASSWORD'] if k in env})

    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}

    print("\n=== Checking licenses for each user ===")
    users_url = "https://graph.microsoft.com/v1.0/users?$top=10&$select=id,displayName,mail,assignedLicenses"
    users_resp = requests.get(users_url, headers=headers)
    if users_resp.status_code == 200:
        users = users_resp.json().get('value', [])
        for user in users:
            print(f"\nUser: {user.get('displayName')} ({user.get('mail')})")
            assigned = user.get('assignedLicenses', [])
            if not assigned:
                print("  No licenses assigned.")
                continue
            print(f"  Number of assigned licenses: {len(assigned)}")
            # For each license, get the sku details (we need to fetch the sku separately)
            # We'll just print the skuIds and then look up the display name if we have a mapping
            sku_ids = [lic['skuId'] for lic in assigned]
            print(f"  SKU IDs: {sku_ids}")
            # We can try to get the subscribed skus for the tenant to map skuId to name
    else:
        print(f"Failed to get users: {users_resp.status_code} - {users_resp.text[:200]}")

    print("\n=== Getting subscribed skus for the tenant (to map skuId to name) ===")
    skus_url = "https://graph.microsoft.com/v1.0/subscribedSkus"
    skus_resp = requests.get(skus_url, headers=headers)
    if skus_resp.status_code == 200:
        skus = skus_resp.json().get('value', [])
        # Create a mapping from skuId to skuPartNumber (or displayName)
        sku_map = {sku['skuId']: sku.get('skuPartNumber') for sku in skus}
        print(f"Found {len(skus)} subscribed SKUs.")
        # Now re-evaluate users with this map
        users_resp = requests.get(users_url, headers=headers)
        if users_resp.status_code == 200:
            users = users_resp.json().get('value', [])
            for user in users:
                print(f"\nUser: {user.get('displayName')} ({user.get('mail')})")
                assigned = user.get('assignedLicenses', [])
                if not assigned:
                    print("  No licenses assigned.")
                    continue
                for lic in assigned:
                    sku_id = lic['skuId']
                    sku_name = sku_map.get(sku_id, 'Unknown')
                    print(f"  - SKU: {sku_name} ({sku_id})")
    else:
        print(f"Failed to get subscribed skus: {skus_resp.status_code} - {skus_resp.text[:200]}")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
