"""Web3/Polygon interaction wrapper — USDC.e balance and approvals.

Connects to Polygon mainnet via Alchemy RPC. Gracefully degrades
when web3 is not installed or keys are not configured.
"""
import logging
import os

log = logging.getLogger("scanner.blockchain")

USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_E_DECIMALS = 6

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


def _get_web3():
    """Get a connected Web3 instance or None."""
    api_key = os.environ.get("ALCHEMY_API_KEY", "")
    if not api_key:
        log.debug("ALCHEMY_API_KEY not set, blockchain features unavailable")
        return None

    try:
        from web3 import Web3
    except ImportError:
        log.warning("web3 package not installed. Run: pip install web3")
        return None

    rpc_url = f"https://polygon-mainnet.g.alchemy.com/v2/{api_key}"
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
