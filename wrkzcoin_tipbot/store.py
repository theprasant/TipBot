from typing import List, Dict
from datetime import datetime
import time, json
import aiohttp, asyncio, aiomysql
from aiomysql.cursors import DictCursor
from discord_webhook import DiscordWebhook
import disnake

from config import config
import sys, traceback
import os.path

# redis
import redis

redis_pool = None
redis_conn = None
redis_expired = 10
pool = None
sys.path.append("..")


def init():
    global redis_pool
    print("PID %d: initializing redis pool..." % os.getpid())
    redis_pool = redis.ConnectionPool(host='localhost', port=6379, decode_responses=True, db=8)

def openRedis():
    global redis_pool, redis_conn
    if redis_conn is None:
        try:
            redis_conn = redis.Redis(connection_pool=redis_pool)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


async def openConnection():
    global pool
    try:
        if pool is None:
            pool = await aiomysql.create_pool(host=config.mysql.host, port=3306, minsize=8, maxsize=16, 
                                                   user=config.mysql.user, password=config.mysql.password,
                                                   db=config.mysql.db, cursorclass=DictCursor, autocommit=True)
    except:
        print("ERROR: Unexpected error: Could not connect to MySql instance.")
        sys.exit()


async def logchanbot(content: str):
    try:
        webhook = DiscordWebhook(url=config.discord.webhook_url, content=f'```{disnake.utils.escape_markdown(content)}```')
        webhook.execute()
    except Exception as e:
        traceback.print_exc(file=sys.stdout)


async def sql_info_by_server(server_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_server WHERE serverid = %s LIMIT 1 """
                await cur.execute(sql, (server_id,))
                result = await cur.fetchone()
                return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return None


async def sql_addinfo_by_server(server_id: str, servername: str, prefix: str, default_coin: str, rejoin: bool = True):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if rejoin:
                    sql = """ INSERT INTO `discord_server` (`serverid`, `servername`, `prefix`, `default_coin`)
                              VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE 
                              `servername` = %s, `prefix` = %s, `default_coin` = %s, `status` = %s """
                    await cur.execute(sql, (server_id, servername[:28], prefix, default_coin, servername[:28], prefix, default_coin, "REJOINED", ))
                    await conn.commit()
                else:
                    sql = """ INSERT INTO `discord_server` (`serverid`, `servername`, `prefix`, `default_coin`)
                              VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE 
                              `servername` = %s, `prefix` = %s, `default_coin` = %s"""
                    await cur.execute(sql, (server_id, servername[:28], prefix, default_coin, servername[:28], prefix, default_coin,))
                    await conn.commit()
    except Exception as e:
        traceback.print_exc(file=sys.stdout)


async def sql_add_messages(list_messages):
    if len(list_messages) == 0:
        return 0
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `discord_messages` (`serverid`, `server_name`, `channel_id`, `channel_name`, `user_id`, 
                          `message_author`, `message_id`, `message_content`, `message_time`)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.executemany(sql, list_messages)
                await conn.commit()
                return cur.rowcount
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return None


async def sql_get_messages(server_id: str, channel_id: str, time_int: int, num_user: int=None):
    global pool
    lapDuration = int(time.time()) - time_int
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                list_talker = []
                if num_user is None:
                    sql = """ SELECT DISTINCT `user_id` FROM discord_messages 
                              WHERE `serverid` = %s AND `channel_id` = %s AND `message_time`>%s """
                    await cur.execute(sql, (server_id, channel_id, lapDuration,))
                    result = await cur.fetchall()
                    if result:
                        for item in result:
                            if int(item['user_id']) not in list_talker:
                                list_talker.append(int(item['user_id']))
                else:
                    sql = """ SELECT `user_id` FROM discord_messages WHERE `serverid` = %s AND `channel_id` = %s 
                              GROUP BY `user_id` ORDER BY max(`message_time`) DESC LIMIT %s """
                    await cur.execute(sql, (server_id, channel_id, num_user,))
                    result = await cur.fetchall()
                    if result:
                        for item in result:
                            if int(item['user_id']) not in list_talker:
                                list_talker.append(int(item['user_id']))
                return list_talker
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return None


async def sql_changeinfo_by_server(server_id: str, what: str, value: str):
    global pool
    if what.lower() in ["servername", "prefix", "default_coin", "tiponly", "numb_user", "numb_bot", "numb_channel", \
    "react_tip", "react_tip_100", "react_tip_coin", "lastUpdate", "botchan", "raffle_channel", "enable_faucet", "enable_game", "enable_market", "enable_trade", "tip_message", \
    "tip_message_by", "tip_notifying_acceptance", "game_2048_channel", "game_bagel_channel", "game_blackjack_channel", "game_dice_channel", \
    "game_maze_channel", "game_slot_channel", "game_snail_channel", "game_sokoban_channel", "game_hangman_channel", "enable_nsfw"]:
        try:
            #print(f"ok try to change {what} to {value}")
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    sql = """ UPDATE discord_server SET `""" + what.lower() + """` = %s WHERE `serverid` = %s """
                    await cur.execute(sql, (value, server_id,))
                    await conn.commit()
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


# TODO: get balance based on various coin, external withdraw, other expenses, tipping out, etc
async def sql_user_balance(userID: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    TOKEN_NAME = coin.upper()
    user_server = user_server.upper()
    token_info = (await get_all_token())[TOKEN_NAME]
    confirmed_depth = token_info['deposit_confirm_depth']
    try:
        await openConnection()
        async with pool.acquire() as conn:
            
            async with conn.cursor() as cur:
                # When sending tx out, (negative)
                sql = """ SELECT SUM(real_amount+real_external_fee) AS SendingOut FROM erc20_external_tx 
                          WHERE `user_id`=%s AND `token_name` = %s """
                await cur.execute(sql, (userID, TOKEN_NAME))
                result = await cur.fetchone()
                if result:
                    SendingOut = result['SendingOut']
                else:
                    SendingOut = 0

                sql = """ SELECT SUM(real_amount) AS Expense FROM `user_balance_mv` WHERE `from_userid`=%s AND `token_name` = %s """
                await cur.execute(sql, (userID, TOKEN_NAME))
                result = await cur.fetchone()
                if result:
                    Expense = result['Expense']
                else:
                    Expense = 0

                sql = """ SELECT SUM(real_amount) AS Income FROM `user_balance_mv` WHERE `to_userid`=%s AND `token_name` = %s """
                await cur.execute(sql, (userID, TOKEN_NAME))
                result = await cur.fetchone()
                if result:
                    Income = result['Income']
                else:
                    Income = 0
                # in case deposit fee -real_deposit_fee
                sql = """ SELECT SUM(real_amount-real_deposit_fee) AS Deposit FROM `erc20_move_deposit` WHERE `user_id`=%s 
                          AND `token_name` = %s AND `confirmed_depth`> %s """
                await cur.execute(sql, (userID, TOKEN_NAME, confirmed_depth))
                result = await cur.fetchone()
                if result:
                    Deposit = result['Deposit']
                else:
                    Deposit = 0

                # pending airdrop
                sql = """ SELECT SUM(real_amount) AS airdropping FROM `discord_airdrop_tmp` WHERE `from_userid`=%s 
                          AND `token_name` = %s AND (`status`=%s OR `status`=%s) """
                await cur.execute(sql, (userID, TOKEN_NAME, "ONGOING", "FAST"))
                result = await cur.fetchone()
                if result:
                    airdropping = result['airdropping']
                else:
                    airdropping = 0

                # pending mathtip
                sql = """ SELECT SUM(real_amount) AS mathtip FROM `discord_mathtip_tmp` WHERE `from_userid`=%s 
                          AND `token_name` = %s AND `status`=%s """
                await cur.execute(sql, (userID, TOKEN_NAME, "ONGOING"))
                result = await cur.fetchone()
                if result:
                    mathtip = result['mathtip']
                else:
                    mathtip = 0

                # pending triviatip
                sql = """ SELECT SUM(real_amount) AS triviatip FROM `discord_triviatip_tmp` WHERE `from_userid`=%s 
                          AND `token_name` = %s AND `status`=%s """
                await cur.execute(sql, (userID, TOKEN_NAME, "ONGOING"))
                result = await cur.fetchone()
                if result:
                    triviatip = result['triviatip']
                else:
                    triviatip = 0

            balance = {}
            balance['Adjust'] = 0
            balance['Expense'] = float("%.3f" % Expense) if Expense else 0
            balance['Income'] = float("%.3f" % Income) if Income else 0
            balance['SendingOut'] = float("%.3f" % SendingOut) if SendingOut else 0
            balance['Deposit'] = float("%.3f" % Deposit) if Deposit else 0
            balance['airdropping'] = float("%.3f" % airdropping) if airdropping else 0
            balance['mathtip'] = float("%.4f" % mathtip) if mathtip else 0
            balance['triviatip'] = float("%.4f" % triviatip) if triviatip else 0
            balance['Adjust'] = float("%.3f" % (balance['Income'] - balance['SendingOut'] - balance['Expense'] + balance['Deposit'] - balance['airdropping'] - balance['mathtip'] - balance['triviatip']))
            # Negative check
            try:
                if balance['Adjust'] < 0:
                    msg_negative = 'Negative balance detected:\nServer:'+user_server+'\nUser: '+str(username)+'\nToken: '+TOKEN_NAME+'\nBalance: '+str(balance['Adjust'])
                    await logchanbot(msg_negative)
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
            return balance
    except Exception as e:
        traceback.print_exc(file=sys.stdout)


# owner message to delete (which bot respond)
async def add_discord_bot_message(message_id: str, guild_id: str, owner_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO discord_bot_message_owner (`message_id`, `guild_id`, `owner_id`, `stored_time`) 
                          VALUES (%s, %s, %s, %s) """
                await cur.execute(sql, (message_id, guild_id, owner_id, int(time.time())))
                await conn.commit()
                return True
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return None


async def get_discord_bot_message(message_id: str, is_deleted: str="NO"):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `discord_bot_message_owner` WHERE `message_id`=%s AND `is_deleted`=%s LIMIT 1 """
                await cur.execute(sql, (message_id, is_deleted))
                result = await cur.fetchone()
                if result: return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return None

async def delete_discord_bot_message(message_id: str, owner_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE `discord_bot_message_owner` SET `is_deleted`=%s, `date_deleted`=%s WHERE `message_id`=%s AND `owner_id`=%s LIMIT 1 """
                await cur.execute(sql, ("YES", int(time.time()), message_id, owner_id))
                await conn.commit()
                return True
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return None
# End owner message

# get coin_setting
async def get_coin_settings(coin_type: str=None):
    global pool
    try:
        sql_coin_type = ""
        if coin_type: sql_coin_type = """ AND `type`='""" + coin_type.upper() + """'"""
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `coin_settings` WHERE `is_maintenance`=%s """ + sql_coin_type
                await cur.execute(sql, (0))
                result = await cur.fetchall()
                if result: return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return []


async def sql_nano_get_user_wallets(coin: str):
    global pool
    COIN_NAME = coin.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM nano_user WHERE `coin_name` = %s """
                await cur.execute(sql, (COIN_NAME,))
                result = await cur.fetchall()
                return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return []


async def sql_get_new_tx_table(notified: str = 'NO', failed_notify: str = 'NO'):
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `discord_notify_new_tx` WHERE `notified`=%s AND `failed_notify`=%s """
                await cur.execute(sql, (notified, failed_notify,))
                result = await cur.fetchall()
                return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return []


async def sql_update_notify_tx_table(payment_id: str, owner_id: str, owner_name: str, notified: str = 'YES', failed_notify: str = 'NO'):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE `discord_notify_new_tx` SET `owner_id`=%s, `owner_name`=%s, `notified`=%s, `failed_notify`=%s, 
                          `notified_time`=%s WHERE `payment_id`=%s """
                await cur.execute(sql, (owner_id, owner_name, notified, failed_notify, float("%.3f" % time.time()), payment_id,))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_userwallet_by_paymentid(paymentid: str, coin: str, coin_family: str, user_server: str):
    COIN_NAME = coin.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = None
                if coin_family in ["TRTL-API", "TRTL-SERVICE", "BCN", "XMR"]:
                    sql = """ SELECT * FROM `cn_user_paymentid` WHERE `paymentid`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "CHIA":
                    sql = """ SELECT * FROM `xch_user` WHERE `balance_wallet_address`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "BTC":
                    # if doge family, address is paymentid
                    sql = """ SELECT * FROM `doge_user` WHERE `balance_wallet_address`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "NANO":
                    # if doge family, address is paymentid
                    sql = """ SELECT * FROM `nano_user` WHERE `balance_wallet_address`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                return result
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return None


# ERC, TRC scan
async def get_txscan_stored_list_erc(net_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `erc20_contract_scan` WHERE `net_name`=%s ORDER BY `blockNumber` DESC LIMIT 4000 """
                await cur.execute(sql, (net_name))
                result = await cur.fetchall()
                if result and len(result) > 0: return {'txHash_unique': [item['contract_blockNumber_Tx_from_to_uniq'] for item in result]}
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return {'txHash_unique': []}


async def get_latest_stored_scanning_height_erc(net_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT MAX(`blockNumber`) as TopBlock FROM `erc20_contract_scan` WHERE `net_name`=%s """
                await cur.execute(sql, (net_name))
                result = await cur.fetchone()
                if result and result['TopBlock']: return int(result['TopBlock'])
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return 1


async def get_monit_contract_tx_insert_erc(list_data):
    global pool
    if len(list_data) == 0:
        return 0
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `erc20_contract_scan` (`net_name`, `contract`, `topics_dump`, `from_addr`, `to_addr`, `blockNumber`, `blockTime`, `transactionHash`, `contract_blockNumber_Tx_from_to_uniq`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.executemany(sql, list_data)
                await conn.commit()
                return cur.rowcount
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        pass
    return 0

# single is working OK
async def get_monit_contract_tx_insert_each(list_data):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `erc20_contract_scan` (`net_name`, `contract`, `topics_dump`, `from_addr`, `to_addr`, `blockNumber`, `blockTime`, `transactionHash`, `contract_blockNumber_Tx_from_to_uniq`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE `update_duplicated`=%s """
                await cur.execute(sql, list_data)
                await conn.commit()
                return cur.rowcount
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        pass
    return 0

async def get_monit_scanning_net_name_update_height(net_name: str, new_height: int):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE `coin_ethscan_setting` SET `scanned_from_height`=%s WHERE `net_name`=%s AND `scanned_from_height`<%s  LIMIT 1 """
                await cur.execute(sql, (new_height, net_name, new_height))
                await conn.commit()
                return new_height
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
    return None


async def erc_get_block_number(url: str, timeout: int=64):
    data = '{"jsonrpc":"2.0", "method":"eth_blockNumber", "params":[], "id":1}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers={'Content-Type': 'application/json'}, json=json.loads(data), timeout=timeout) as response:
                if response.status == 200:
                    res_data = await response.read()
                    res_data = res_data.decode('utf-8')
                    await session.close()
                    decoded_data = json.loads(res_data)
                    if decoded_data and 'result' in decoded_data:
                        return int(decoded_data['result'], 16)
    except asyncio.TimeoutError:
        print('TIMEOUT: get block number {}s'.format(timeout))
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        await logchanbot(traceback.format_exc())
    return None


async def erc_get_block_info(url: str, height: int, timeout: int=32):
    try:
        data = '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["'+str(hex(height))+'", false],"id":1}'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers={'Content-Type': 'application/json'}, json=json.loads(data), timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        await session.close()
                        decoded_data = json.loads(res_data)
                        if decoded_data and 'result' in decoded_data:
                            return decoded_data['result']
        except asyncio.TimeoutError:
            print('TIMEOUT: erc_get_block_info for {}s'.format(timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
    except ValueError:
        traceback.print_exc(file=sys.stdout)
    return None


# TODO: this is for ERC-20 only
async def http_wallet_getbalance(url: str, address: str, coin: str, contract: str=None, timeout: int = 64) -> Dict:
    TOKEN_NAME = coin.upper()
    if contract is None:
        data = '{"jsonrpc":"2.0","method":"eth_getBalance","params":["'+address+'", "latest"],"id":1}'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers={'Content-Type': 'application/json'}, json=json.loads(data), timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        decoded_data = json.loads(res_data)
                        if decoded_data and 'result' in decoded_data:
                            return int(decoded_data['result'], 16)
        except asyncio.TimeoutError:
            print('TIMEOUT: get balance {} for {}s'.format(TOKEN_NAME, timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
    else:
        data = '{"jsonrpc":"2.0","method":"eth_call","params":[{"to": "'+contract+'", "data": "0x70a08231000000000000000000000000'+address[2:]+'"}, "latest"],"id":1}'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers={'Content-Type': 'application/json'}, json=json.loads(data), timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        decoded_data = json.loads(res_data)
                        if decoded_data and 'result' in decoded_data:
                            return int(decoded_data['result'], 16)
        except asyncio.TimeoutError:
            print('TIMEOUT: get balance {} for {}s'.format(TOKEN_NAME, timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
    return None
