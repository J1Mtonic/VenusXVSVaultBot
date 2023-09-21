import os
import requests
import json
import time
import schedule
import logging
from web3 import Web3, HTTPProvider

path_to_key = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json")
path_to_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xvs_vault_stakers.json")
path_to_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xvs_vault_bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
handler = logging.FileHandler(path_to_log, encoding="utf-8")
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)
logger.addHandler(handler)

contractAddress = '0x051100480289e704d20e9db4804837068f3f9204'
depositMethodID = '0x0efe6a8b'
withdrawalMethodID = '0x7ac92456'
provider_url = "https://bsc-dataseed.binance.org" 
#provider_url = "https://bsc.meowrpc.com"
w3 = Web3(HTTPProvider(provider_url))
contractABI = [
    {
        "constant": True,
        "inputs": [
            {"internalType": "address", "name": "_rewardToken", "type": "address"},
            {"internalType": "uint256", "name": "_pid", "type": "uint256"},
            {"internalType": "address", "name": "_user", "type": "address"}
        ],
        "name": "getUserInfo",
        "outputs": [
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "uint256", "name": "rewardDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "pendingWithdrawals", "type": "uint256"}
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]
contract = w3.eth.contract(address=Web3.to_checksum_address(contractAddress), abi=contractABI)
jsonData = {}
with open(path_to_key, 'r') as f:
    keys = json.load(f)
apiToken = keys["apiToken"]
TELEGRAM_TOKEN = keys["TELEGRAM_TOKEN"]
CHAT_ID = keys["CHAT_ID"]

def validate_json_structure(data):
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object.")
    if not any(key in data for key in ["result", "message", "status"]):
        raise ValueError("JSON does not contain common keys: 'result', 'message', or 'status'.")

def safe_request(url):
    while True:
        try:
            response = requests.get(url)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                json_data = response.json()
                try:
                    validate_json_structure(json_data)
                    return json_data
                except ValueError as ve:
                    logger.error(f"Validation error: {ve}. Response content: {json_data}")
                    raise
            else:
                raise ValueError(f"Unexpected content type {content_type} from {url}")
        except (requests.RequestException, ValueError) as e:
            error_message = f"Error fetching data from {url}. Error: {e}. Retrying in 60 seconds..."
            logger.info(error_message)
            logging.error(error_message)
            time.sleep(60)

def fetchData():
    global jsonData
    try:
        with open(path_to_file, "r") as file:
            jsonData = json.load(file)
    except Exception as e:
        logger.info(f"Error reading file: {e}")
        response = safe_request("https://raw.githubusercontent.com/J1Mtonic/VenusXVSVaultBot/main/xvs_vault_stakers.json")
        jsonData = response.json()
    updateData()

def saveData():
    global jsonData
    with open(path_to_file, "w") as file:
        json.dump(jsonData, file, indent=2)

def getCurrentBlock():
    return w3.eth.block_number

def fetchTransactionsRecursively(startBlock, endBlock, methodID):
    try:
        apiUrl = f"https://api.bscscan.com/api?module=account&action=txlist&address={contractAddress}&startblock={startBlock}&endblock={endBlock}&page=1&offset=10000&sort=asc&apikey={apiToken}"
        data = safe_request(apiUrl)
        if data and data.get("message", "") == "Result window is too large":
            midBlock = (startBlock + endBlock) // 2
            firstHalf = fetchTransactionsRecursively(startBlock, midBlock, methodID)
            secondHalf = fetchTransactionsRecursively(midBlock + 1, endBlock, methodID)
            return firstHalf + secondHalf
        return [tx for tx in data.get("result", []) if tx["input"].startswith(methodID)]    
    except (KeyError, TypeError) as e:
        error_message = f"Unexpected JSON structure or value. Error: {e}. Retrying..."
        logger.info(error_message)
        logging.error(error_message)
        return []

def updateData():
    global jsonData
    #logger.info("Updating data...")

    previousData = jsonData.copy()

    currentBlock = getCurrentBlock()
    logger.info(f"Current block: {currentBlock}")

    lastBlock = jsonData.get("metadata", {}).get("last_block_evaluated", 0)
    #logger.info(f"Last evaluated block: {lastBlock}")

    depositTransactions = fetchTransactionsRecursively(lastBlock + 1, currentBlock, depositMethodID)
    withdrawalTransactions = fetchTransactionsRecursively(lastBlock + 1, currentBlock, withdrawalMethodID)

    allTransactions = depositTransactions + withdrawalTransactions

    for tx in allTransactions:
        user = tx["from"]
        checksum_address = Web3.to_checksum_address('0xcf6bb5389c92bdda8a3747ddb454cb7a64626c63')
        checksum_user = Web3.to_checksum_address(user)
        userInfo = contract.functions.getUserInfo(checksum_address, 0, checksum_user).call()
        amount = userInfo[0] / (10 ** 18)
        difference = 0

        existingUser = next((item for item in jsonData["transactions"] if item["user"] == user), None)
        truncated_address = user[:6] + "..." + user[-4:]
        debank_url = f"https://debank.com/profile/{user}"

        if existingUser:
            previousAmount = existingUser["amount"]
            difference = amount - previousAmount
            logger.info(f"User: {user} Difference: {difference}")
            if amount == 0:
                jsonData["transactions"] = [item for item in jsonData["transactions"] if item["user"] != user]
            else:
                existingUser["amount"] = amount
                logger.info(f"User: {user} updated with new amount: {amount}")

            if abs(difference) >= 1000:
                formatted_difference = format(int(abs(difference)), ',')
                formatted_amount = format(int(amount), ',')
                if previousAmount < 30000:
                    if difference > 0:
                        send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"✴️🐬 [{truncated_address}]({debank_url}) Added _{formatted_difference} XVS_\n🔒 Staking: _{formatted_amount} XVS_")
                    else:
                        send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"🚨🐬❕❕ [{truncated_address}]({debank_url}) Withdraw _{formatted_difference} XVS_\n🔒 Staking: _{formatted_amount} XVS_")
                else:
                    if difference > 0:
                        send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"✴️🐳❗ [{truncated_address}]({debank_url}) Added _{formatted_difference} XVS_\n🔒 Staking: _{formatted_amount} XVS_")
                    else:
                        send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"🚨🐳❗❗ [{truncated_address}]({debank_url}) Withdraw _{formatted_difference} XVS_\n🔒 Staking: _{formatted_amount} XVS_")
        elif amount > 0:
            jsonData["transactions"].append({"user": user, "amount": amount})
            logger.info(f"User: {user} added with amount: {amount}")
            formatted_amount = format(int(amount), ',')
            if amount > 30000:
                send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"❇️🐳❗❗ [{truncated_address}]({debank_url}) New Vault User!\n🔒 Staking: _{formatted_amount} XVS_")
            elif amount > 1000:
                send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, f"❇️🐬❕ [{truncated_address}]({debank_url}) New Vault User!\n🔒 Staking: _{formatted_amount} XVS_") 

    jsonData["transactions"].sort(key=lambda x: x["amount"], reverse=True)

    updatedActiveUniqueUsers = len([user for user in jsonData["transactions"] if user["amount"] > 0])
    updatedTotalStaked = sum([user["amount"] for user in jsonData["transactions"]])

    summary = {
        "active_unique_deposit_users": updatedActiveUniqueUsers,
        "last_block_evaluated": currentBlock,
        "total_staked": updatedTotalStaked
    }
    jsonData["metadata"] = summary

    if jsonData["transactions"] != previousData.get("transactions", {}):
        logger.info("Data updated successfully.")        
    else:
        #logger.info("No new updates found.")
        pass

    saveData()

def displaySummary():
    thresholds = [300000, 100000, 50000, 10000, 5000, 1000]
    messages = []
    active_users = format(jsonData['metadata']['active_unique_deposit_users'], ',')
    xvs_staked = format(int(jsonData['metadata']['total_staked']), ',')
    messages.append(f"🔒 *XVS Staked:* _{xvs_staked}_ XVS")
    messages.append(f"👥 *Vault Users:* _{active_users}_")
    messages.append("\n💰 *Staking Breakdown:*")
    for threshold in thresholds:
        count = len([user for user in jsonData["transactions"] if user["amount"] >= threshold])
        threshold_value = threshold // 1000
        messages.append(f"💎 *{threshold_value}k%2B:* _{count} users_")    
    full_message = '\n'.join(messages)
    send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, full_message)

def displayUsers():
    threshold = 30000
    filteredUsers = [user for user in jsonData["transactions"] if user["amount"] > threshold]
    messages = []
    messages.append("🐳 *Vault Whales (30k%2B):*")
    messages.append("\n🔗 *Address*            🏦 *Staked*")
    for user in filteredUsers:
        truncated_address = user['user'][:6] + "..." + user['user'][-4:]
        formatted_amount = format(int(user['amount']), ',')
        debank_url = f"https://debank.com/profile/{user['user']}"
        messages.append(f"[{truncated_address}]({debank_url})   -   _{formatted_amount} XVS_")
    full_message = '\n'.join(messages)
    send_telegram_message(CHAT_ID, TELEGRAM_TOKEN, full_message, parse_mode="Markdown")

def send_telegram_message(chat_id, token, message, parse_mode=None):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}&parse_mode=Markdown&disable_web_page_preview=true"
    if parse_mode:
        base_url += f"&parse_mode={parse_mode}"
    try:
        response = safe_request(base_url)
        return response
    except Exception as e:
        logger.info(f"Error sending Telegram message: {e}")
        return None

def daily_tasks():
    displaySummary()
    displayUsers()

if __name__ == "__main__":
    try:
        fetchData()
        schedule.every().day.at("07:30").do(daily_tasks)
        updateData_counter = 0
        while True:
            schedule.run_pending()
            if updateData_counter >= 10:
                updateData()
                updateData_counter = 0
            time.sleep(1)
            updateData_counter += 1
    except Exception as e:
        logging.error(f"Unexpected error: {e}")

