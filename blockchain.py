"""Web3/Polygon interaction wrapper — USDC.e balance and approvals.

Connects to Polygon mainnet via Alchemy RPC. Gracefully degrades
when web3 is not installed or keys are not configured.
"""
import json
import logging
import os
import requests

log = logging.getLogger("scanner.blockchain")

USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_E_DECIMALS = 6

# Polymarket Core Contracts
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Minimal ERC-20 ABI for balanceOf and approve
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


def _get_rpc_url():
    """Get the Alchemy RPC URL."""
    api_key = os.environ.get("ALCHEMY_API_KEY", "")
    if not api_key:
        return None
    return f"https://polygon-mainnet.g.alchemy.com/v2/{api_key}"


def _get_web3():
    """Get a connected Web3 instance or None."""
    rpc_url = _get_rpc_url()
    if not rpc_url:
        log.debug("ALCHEMY_API_KEY not set, blockchain features unavailable")
        return None

    try:
        from web3 import Web3
    except ImportError:
        log.warning("web3 package not installed. Run: pip install web3")
        return None

    w3 = Web3(Web3.HTTPProvider(rpc_url))

    if not w3.is_connected():
        log.error("Failed to connect to Polygon RPC at %s", rpc_url[:50] + "...")
        return None

    log.debug("Connected to Polygon via Alchemy")
    return w3


def get_wallet_address():
    """Derive wallet address from POLYMARKET_PRIVATE_KEY.

    Returns:
        Checksummed address string, or None if key not configured.
    """
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        log.debug("POLYMARKET_PRIVATE_KEY not set, no wallet address available")
        return None

    try:
        from eth_account import Account
    except ImportError:
        log.warning("eth_account not installed. Run: pip install web3")
        return None

    try:
        account = Account.from_key(private_key)
        log.debug("Wallet address: %s", account.address)
        return account.address
    except Exception as e:
        log.error("Failed to derive wallet address: %s", e)
        return None


def get_usdc_balance(wallet_address):
    """Get USDC.e balance for a wallet on Polygon.

    Args:
        wallet_address: checksummed Ethereum address.

    Returns:
        Balance in USD (float). Returns 0.0 if unavailable.
    """
    w3 = _get_web3()
    if not w3:
        log.warning("Web3 not available, returning 0 balance")
        return 0.0

    try:
        contract = w3.eth.contract(
            address=w3.to_checksum_address(USDC_E_ADDRESS),
            abi=ERC20_ABI,
        )
        raw_balance = contract.functions.balanceOf(
            w3.to_checksum_address(wallet_address)
        ).call()
        balance = raw_balance / (10 ** USDC_E_DECIMALS)
        log.info("USDC.e balance for %s: $%.2f", wallet_address[:10] + "...", balance)
        return balance
    except Exception as e:
        log.error("Failed to fetch USDC.e balance: %s", e)
        return 0.0


def approve_token(spender, amount):
    """Approve a spender to transfer USDC.e on behalf of the wallet.

    Args:
        spender: address to approve (e.g., Polymarket exchange contract).
        amount: amount in USD to approve (converted to raw units internally).

    Returns:
        dict with tx_hash on success, or error info.
    """
    w3 = _get_web3()
    if not w3:
        return {"ok": False, "error": "Web3 not available"}

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set"}

    try:
        from eth_account import Account

        account = Account.from_key(private_key)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(USDC_E_ADDRESS),
            abi=ERC20_ABI,
        )

        raw_amount = int(amount * (10 ** USDC_E_DECIMALS))
        tx = contract.functions.approve(
            w3.to_checksum_address(spender),
            raw_amount,
        ).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        hex_hash = tx_hash.hex()

        log.info("Approval tx sent: %s (spender=%s amount=$%.2f)",
                 hex_hash, spender[:10] + "...", amount)

        return {"ok": True, "tx_hash": hex_hash, "amount": amount, "spender": spender}

    except Exception as e:
        log.error("Token approval failed: %s", e)
        return {"ok": False, "error": str(e)}


def trace_transactions(address, limit=50):
    """Fetch recent asset transfers for an address using Alchemy Enhanced API.

    Returns:
        List of transfer objects or empty list.
    """
    rpc_url = _get_rpc_url()
    if not rpc_url:
        return []

    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "alchemy_getAssetTransfers",
        "params": [
            {
                "fromAddress": address.lower(),
                "category": ["external", "erc20", "erc1155"],
                "maxCount": f"0x{limit:x}",
                "withMetadata": True,
            }
        ],
    }

    try:
        resp = requests.post(rpc_url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        transfers = data.get("result", {}).get("transfers", [])

        # Also get 'to' transfers
        payload["params"][0].pop("fromAddress")
        payload["params"][0]["toAddress"] = address.lower()
        resp = requests.post(rpc_url, json=payload, timeout=15)
        if resp.ok:
            to_transfers = resp.json().get("result", {}).get("transfers", [])
            transfers.extend(to_transfers)

        # Sort by block number desc
        transfers.sort(key=lambda x: int(x.get("blockNum", "0x0"), 16), reverse=True)
        return transfers[:limit]
    except Exception as e:
        log.error("Failed to trace transactions for %s: %s", address, e)
        return []


def reverse_engineer_patterns(address):
    """Analyze on-chain behavior to identify winner patterns.

    Checks:
    1. Interaction frequency with CTF Exchange.
    2. Redemption patterns (claiming winnings).
    3. Use of proxy wallets (Gnosis Safe).
    4. Asset variety (USDC.e vs other collateral).

    Returns:
        dict with pattern analysis.
    """
    transfers = trace_transactions(address, limit=100)
    if not transfers:
        return {"ok": False, "error": "No on-chain data found"}

    analysis = {
        "address": address,
        "total_transfers_sampled": len(transfers),
        "polymarket_interactions": 0,
        "redemption_count": 0,
        "uses_proxy": False,
        "contracts_interacted": set(),
        "common_tokens": set(),
        "avg_tx_frequency_blocks": 0,
    }

    blocks = []
    polymarket_contracts = {
        CTF_EXCHANGE.lower(),
        NEG_RISK_EXCHANGE.lower(),
        CTF_CONTRACT.lower(),
        USDC_E_ADDRESS.lower(),
    }

    for tx in transfers:
        to_addr = (tx.get("to") or "").lower()
        from_addr = (tx.get("from") or "").lower()
        asset = tx.get("asset", "")
        block = int(tx.get("blockNum", "0x0"), 16)
        blocks.append(block)

        if asset:
            analysis["common_tokens"].add(asset)

        if to_addr in polymarket_contracts or from_addr in polymarket_contracts:
            analysis["polymarket_interactions"] += 1

        if to_addr:
            analysis["contracts_interacted"].add(to_addr)

        # Redemptions often involve CTF contract sending tokens or burning positions
        if from_addr == CTF_CONTRACT.lower() and tx.get("category") == "erc1155":
            analysis["redemption_count"] += 1

    # Check for Gnosis Safe / Proxy pattern
    # (High interaction with a specific contract that isn't a known exchange)
    for contract in analysis["contracts_interacted"]:
        if contract not in polymarket_contracts and "proxy" in contract:  # heuristic
            analysis["uses_proxy"] = True

    if len(blocks) > 1:
        analysis["avg_tx_frequency_blocks"] = (max(blocks) - min(blocks)) / len(blocks)

    # Convert sets to lists for JSON serialization
    analysis["contracts_interacted"] = list(analysis["contracts_interacted"])[:10]
    analysis["common_tokens"] = list(analysis["common_tokens"])
    analysis["ok"] = True

    log.info("Reverse-engineered patterns for %s: %d PM interactions, %d redemptions",
             address[:12], analysis["polymarket_interactions"], analysis["redemption_count"])

    return analysis
