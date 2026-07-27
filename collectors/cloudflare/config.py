from lib.secrets import load_env

ENV = load_env()

ZONE_ID = ENV["ZONE_ID"]
ACCOUNT_ID = ENV["CLOUDFLARE_ACCOUNT_ID"]

WORKSPACE = None