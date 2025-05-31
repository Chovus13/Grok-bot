# config.py
def get_config(key: str, default: any = None) -> any:
    # Implementacija za čitanje iz env, fajla, ili DB
    # Primer:
    config = {
        "api_key": "your_testnet_api_key",
        "api_secret": "your_testnet_api_secret",
        "available_pairs": "BTC/USDT,ETH/USDT,SOL/USDT",
        "leverage_BTC_USDT": 10,
        "leverage_ETH_USDT": 10,
        "leverage_SOL_USDT": 10,
        "balance": "1000"  # Fallback balans
    }
    return config.get(key, default)