from discord_webhook import DiscordWebhook
import discord

from typing import List, Dict
from datetime import datetime
import time
import json
import asyncio
import aiomysql
from aiomysql.cursors import DictCursor

import daemonrpc_client, rpc_client, wallet, walletapi, addressvalidation
from config import config
import sys, traceback
import os.path

# Encrypt
from cryptography.fernet import Fernet

# MySQL
import pymysql

# redis
import redis

redis_pool = None
redis_conn = None
redis_expired = 120

FEE_PER_BYTE_COIN = config.Fee_Per_Byte_Coin.split(",")

pool = None
pool_cmc = None

#conn = None
sys.path.append("..")

ENABLE_COIN = config.Enable_Coin.split(",")
ENABLE_XMR = config.Enable_Coin_XMR.split(",")
ENABLE_COIN_DOGE = config.Enable_Coin_Doge.split(",")
ENABLE_COIN_NANO = config.Enable_Coin_Nano.split(",")

XS_COIN = ["DEGO"]
ENABLE_SWAP = config.Enabe_Swap_Coin.split(",")


# Coin using wallet-api
WALLET_API_COIN = config.Enable_Coin_WalletApi.split(",")

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


async def logchanbot(content: str):
    filterword = config.discord.logfilterword.split(",")
    for each in filterword:
        content = content.replace(each, config.discord.filteredwith)
    try:
        webhook = DiscordWebhook(url=config.discord.botdbghook, content=f'```{discord.utils.escape_markdown(content)}```')
        webhook.execute()
    except Exception as e:
        traceback.print_exc(file=sys.stdout)


# openConnection
async def openConnection():
    global pool
    try:
        if pool is None:
            pool = await aiomysql.create_pool(host=config.mysql.host, port=3306, minsize=6, maxsize=12, 
                                                   user=config.mysql.user, password=config.mysql.password,
                                                   db=config.mysql.db, autocommit=True, cursorclass=DictCursor)
    except:
        print("ERROR: Unexpected error: Could not connect to MySql instance.")
        await logchanbot(traceback.format_exc())


# openConnection_cmc
async def openConnection_cmc():
    global pool_cmc
    try:
        if pool_cmc is None:
            pool_cmc = await aiomysql.create_pool(host=config.mysql_cmc.host, port=3306, minsize=2, maxsize=4, 
                                                       user=config.mysql_cmc.user, password=config.mysql_cmc.password,
                                                       db=config.mysql_cmc.db, cursorclass=DictCursor)
    except:
        print("ERROR: Unexpected error: Could not connect to MySql instance.")
        await logchanbot(traceback.format_exc())


async def get_coingecko_coin(coin: str):
    global pool_cmc
    try:
        await openConnection_cmc()
        async with pool_cmc.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM coingecko_v2 WHERE `symbol`=%s ORDER BY `id` DESC LIMIT 1 """
                await cur.execute(sql, (coin.lower()))
                result = await cur.fetchone()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def get_all_user_balance_address(coin: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT `coin_name`, `balance_wallet_address`, `balance_wallet_address_ch`,`privateSpendKey` FROM `cn_user` WHERE `coin_name` = %s"""
                await cur.execute(sql, (coin))
                result = await cur.fetchall()
                listAddr=[]
                for row in result:
                    listAddr.append({'address':row['balance_wallet_address'], 'scanHeight': row['balance_wallet_address_ch'], 'privateSpendKey': decrypt_string(row['privateSpendKey'])})
                return listAddr
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_nano_update_balances(coin: str):
    global pool, redis_conn
    updated = 0
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","BAN")
    get_balance = await wallet.nano_get_wallet_balance_elements(COIN_NAME)
    all_user_info = await sql_nano_get_user_wallets(COIN_NAME)
    all_deposit_address = {}
    all_deposit_address_keys = []
    if all_user_info and len(all_user_info) > 0:
        all_deposit_address_keys = [each['balance_wallet_address'] for each in all_user_info]
        for each in all_user_info:
            all_deposit_address[each['balance_wallet_address']] = each
    if get_balance and len(get_balance) > 0:
        for address, balance in get_balance.items():
            try:
                # if bigger than minimum deposit, and no pending and the address is in user database addresses
                if int(balance['balance']) >= getattr(getattr(config,"daemon"+COIN_NAME),"min_deposit", 100000000000000000000000000000) \
                and int(balance['pending']) == 0 and address in all_deposit_address_keys:
                    # let's move balance to main_address
                    try:
                        main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
                        move_to_deposit = await wallet.nano_sendtoaddress(address, main_address, int(balance['balance']), COIN_NAME)
                        # add to DB
                        if move_to_deposit:
                            try:
                                await openConnection()
                                async with pool.acquire() as conn:
                                    async with conn.cursor() as cur:
                                        sql = """ INSERT INTO nano_move_deposit (`coin_name`, `user_id`, `balance_wallet_address`, `to_main_address`, `amount`, `decimal`, `block`, `time_insert`) 
                                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                                        await cur.execute(sql, (COIN_NAME, all_deposit_address[address]['user_id'], address, main_address, int(balance['balance']),  wallet.get_decimal(COIN_NAME), move_to_deposit['block'], int(time.time()), ))
                                        await conn.commit()
                                        updated += 1
                                        # add to notification list also
                                        # txid = new block ID
                                        # payment_id = deposit address
                                        sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, `payment_id`, `amount`, `decimal`) 
                                                  VALUES (%s, %s, %s, %s, %s) """
                                        await cur.execute(sql, (COIN_NAME, move_to_deposit['block'], address, int(balance['balance']), wallet.get_decimal(COIN_NAME)))
                                        await conn.commit()
                            except Exception as e:
                                await logchanbot(traceback.format_exc())
                    except Exception as e:
                        await logchanbot(traceback.format_exc())
            except Exception as e:
                await logchanbot(traceback.format_exc())
    return updated


async def sql_nano_balance(userID: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_COIN_NANO:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT SUM(amount) AS Expense FROM nano_mv_tx WHERE `from_userid`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Expense = result['Expense']
                else:
                    Expense = 0

                sql = """ SELECT SUM(amount) AS Income FROM nano_mv_tx WHERE `to_userid`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Income = result['Income']
                else:
                    Income = 0

                sql = """ SELECT SUM(amount) AS TxExpense FROM nano_external_tx WHERE `user_id`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    TxExpense = result['TxExpense']
                else:
                    TxExpense = 0

                # Credit by admin is positive (Positive)
                sql = """ SELECT SUM(amount) AS Credited FROM credit_balance WHERE `coin_name`=%s AND `to_userid`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Credited = result['Credited']
                else:
                    Credited = 0

                # nano_move_deposit by admin is positive (Positive)
                sql = """ SELECT SUM(amount) AS Deposited FROM nano_move_deposit WHERE `coin_name`=%s AND `user_id`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Deposited = result['Deposited']
                else:
                    Deposited = 0

                # Voucher create (Negative)
                sql = """ SELECT SUM(amount) AS Expended_Voucher FROM cn_voucher 
                          WHERE `coin_name`=%s AND `user_id`=%s AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Expended_Voucher = result['Expended_Voucher']
                else:
                    Expended_Voucher = 0

                # Game Credit
                sql = """ SELECT SUM(won_amount) AS GameCredit FROM discord_game WHERE `coin_name`=%s AND `played_user`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    GameCredit = result['GameCredit']
                else:
                    GameCredit = 0

                balance = {}
                balance['Deposited'] = Deposited or 0
                balance['Expense'] = Expense or 0
                balance['Income'] = Income or 0
                balance['TxExpense'] = TxExpense or 0
                balance['Credited'] = Credited if Credited else 0
                balance['GameCredit'] = GameCredit if GameCredit else 0
                balance['Expended_Voucher'] = Expended_Voucher if Expended_Voucher else 0
                balance['Adjust'] = int(balance['Deposited']) + int(balance['Credited']) + int(balance['GameCredit']) \
                + int(balance['Income']) - int(balance['Expense']) - int(balance['TxExpense']) - int(balance['Expended_Voucher'])
                return balance
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_nano_get_user_wallets(coin: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","BAN")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM nano_user WHERE `coin_name` = %s """
                await cur.execute(sql, (COIN_NAME,))
                result = await cur.fetchall()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


# NANO Based
async def sql_mv_nano_single(user_from: str, to_user: str, amount: float, coin: str, tiptype: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","NANO")
    if coin_family != "NANO":
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO nano_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_from, to_user, amount, wallet.get_decimal(COIN_NAME), tiptype.upper(), int(time.time()),))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_mv_nano_multiple(user_from: str, user_tos, amount_each: float, coin: str, tiptype: str):
    # user_tos is array "account1", "account2", ....
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","NANO")
    if coin_family != "NANO":
        return False
    if tiptype.upper() not in ["TIPS", "TIPALL", "FREETIP", "FREETIPS"]:
        return False
    values_str = []
    currentTs = int(time.time())
    for item in user_tos:
        values_str.append(f"('{COIN_NAME}', '{user_from}', '{item}', {amount_each}, {wallet.get_decimal(COIN_NAME)}, '{tiptype.upper()}', {currentTs})\n")
    values_sql = "VALUES " + ",".join(values_str)
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO nano_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`) 
                          """+values_sql+""" """
                await cur.execute(sql,)
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_external_nano_single(user_from: str, amount: int, to_address: str, coin: str, tiptype: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","NANO")
    if coin_family != "NANO":
        return False
    if tiptype.upper() not in ["SEND", "WITHDRAW"]:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if coin_family == "NANO":
                    main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
                    tx_hash = await wallet.nano_sendtoaddress(main_address, to_address, amount, COIN_NAME)
                    if tx_hash:
                        updateTime = int(time.time())
                        async with conn.cursor() as cur: 
                            sql = """ INSERT INTO nano_external_tx (`coin_name`, `user_id`, `amount`, `decimal`, `to_address`, 
                                      `type`, `date`, `tx_hash`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, user_from, amount, wallet.get_decimal(COIN_NAME), to_address, tiptype.upper(), int(time.time()), tx_hash['block'],))
                            await conn.commit()
                            return tx_hash
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_update_balances(coin: str = None):
    global pool, redis_conn
    updateTime = int(time.time())
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")

    gettopblock = None
    timeout = 12
    try:
        if COIN_NAME not in ENABLE_COIN_DOGE:
            gettopblock = await daemonrpc_client.gettopblock(COIN_NAME, time_out=timeout)
        else:
            gettopblock = await rpc_client.call_doge('getblockchaininfo', COIN_NAME)
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        await logchanbot(traceback.format_exc())

    height = None
    if gettopblock:
        if coin_family in ["TRTL", "BCN", "XMR"]:
            height = int(gettopblock['block_header']['height'])
        elif coin_family == "DOGE":
            height = int(gettopblock['blocks'])
        # store in redis
        try:
            openRedis()
            if redis_conn:
                redis_conn.set(f'TIPBOT:DAEMON_HEIGHT_{COIN_NAME}', str(height))
        except Exception as e:
            await logchanbot(traceback.format_exc())
    else:
        try:
            openRedis()
            if redis_conn and redis_conn.exists(f'TIPBOT:DAEMON_HEIGHT_{COIN_NAME}'):
                height = int(redis_conn.get(f'TIPBOT:DAEMON_HEIGHT_{COIN_NAME}'))
        except Exception as e:
            await logchanbot(traceback.format_exc())

    if coin_family in ["TRTL", "BCN"]:
        #print('SQL: Updating get_transfers '+COIN_NAME)
        if COIN_NAME in WALLET_API_COIN:
            get_transfers = await walletapi.walletapi_get_transfers(COIN_NAME)
            try:
                list_balance_user = {}
                if get_transfers and len(get_transfers) >= 1:
                    await openConnection()
                    async with pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            sql = """ SELECT * FROM cnoff_get_transfers WHERE `coin_name` = %s """
                            await cur.execute(sql, (COIN_NAME,))
                            result = await cur.fetchall()
                            d = [i['txid'] for i in result]
                            # print('=================='+COIN_NAME+'===========')
                            # print(d)
                            # print('=================='+COIN_NAME+'===========')
                            for tx in get_transfers:
                                # Could be one block has two or more tx with different payment ID
                                # add to balance only confirmation depth meet
                                if len(tx['transfers']) > 0 and height >= int(tx['blockHeight']) + wallet.get_confirm_depth(COIN_NAME) \
                                and tx['transfers'][0]['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) and 'paymentID' in tx:
                                    if ('paymentID' in tx) and (tx['paymentID'] in list_balance_user):
                                        if tx['transfers'][0]['amount'] > 0:
                                            list_balance_user[tx['paymentID']] += tx['transfers'][0]['amount']
                                    elif ('paymentID' in tx) and (tx['paymentID'] not in list_balance_user):
                                        if tx['transfers'][0]['amount'] > 0:
                                            list_balance_user[tx['paymentID']] = tx['transfers'][0]['amount']
                                    try:
                                        if tx['hash'] not in d:
                                            addresses = tx['transfers']
                                            address = ''
                                            for each_add in addresses:
                                                if len(each_add['address']) > 0: address = each_add['address']
                                                break
                                                    
                                            sql = """ INSERT IGNORE INTO cnoff_get_transfers (`coin_name`, `txid`, 
                                            `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                            await cur.execute(sql, (COIN_NAME, tx['hash'], tx['paymentID'], tx['blockHeight'], tx['timestamp'],
                                                                    tx['transfers'][0]['amount'], tx['fee'], wallet.get_decimal(COIN_NAME), address, int(time.time())))
                                            await conn.commit()
                                            # add to notification list also
                                            sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, 
                                            `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                            VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                            await cur.execute(sql, (COIN_NAME, tx['hash'], tx['paymentID'], tx['blockHeight'],
                                                                    tx['transfers'][0]['amount'], tx['fee'], wallet.get_decimal(COIN_NAME)))
                                            await conn.commit()
                                    except pymysql.err.Warning as e:
                                        await logchanbot(traceback.format_exc())
                                    except Exception as e:
                                        await logchanbot(traceback.format_exc())
                                elif len(tx['transfers']) > 0 and height < int(tx['blockHeight']) + wallet.get_confirm_depth(COIN_NAME) and \
                                tx['transfers'][0]['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) and 'paymentID' in tx:
                                    # add notify to redis and alert deposit. Can be clean later?
                                    if config.notify_new_tx.enable_new_no_confirm == 1:
                                        key_tx_new = 'TIPBOT:NEWTX:NOCONFIRM'
                                        key_tx_json = 'TIPBOT:NEWTX:' + tx['hash']
                                        try:
                                            openRedis()
                                            if redis_conn and redis_conn.llen(key_tx_new) > 0:
                                                list_new_tx = redis_conn.lrange(key_tx_new, 0, -1)
                                                if list_new_tx and len(list_new_tx) > 0 and tx['hash'] not in list_new_tx:
                                                    redis_conn.lpush(key_tx_new, tx['hash'])
                                                    redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['hash'], 'payment_id': tx['paymentID'], 'height': tx['blockHeight'],
                                                                                            'amount': tx['transfers'][0]['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                            elif redis_conn and redis_conn.llen(key_tx_new) == 0:
                                                redis_conn.lpush(key_tx_new, tx['hash'])
                                                redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['hash'], 'payment_id': tx['paymentID'], 'height': tx['blockHeight'],
                                                                                        'amount': tx['transfers'][0]['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                        except Exception as e:
                                            await logchanbot(traceback.format_exc())
                if list_balance_user and len(list_balance_user) > 0:
                    await openConnection()
                    async with pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            sql = """ SELECT coin_name, payment_id, SUM(amount) AS txIn FROM cnoff_get_transfers 
                                      WHERE coin_name = %s AND amount > 0 
                                      GROUP BY payment_id """
                            await cur.execute(sql, (COIN_NAME,))
                            result = await cur.fetchall()
                            timestamp = int(time.time())
                            list_update = []
                            if result and len(result) > 0:
                                for eachTxIn in result:
                                    list_update.append((eachTxIn['txIn'], timestamp, eachTxIn['payment_id']))
                                await cur.executemany(""" UPDATE cnoff_user_paymentid SET `actual_balance` = %s, `lastUpdate` = %s 
                                                          WHERE paymentid = %s """, list_update)
                                await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
        else:
            get_transfers = await wallet.getTransactions(COIN_NAME, int(height)-100000, 100000)
            try:
                list_balance_user = {}
                if get_transfers and len(get_transfers) >= 1:
                    await openConnection()
                    async with pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            sql = """ SELECT * FROM cnoff_get_transfers WHERE `coin_name` = %s """
                            await cur.execute(sql, (COIN_NAME,))
                            result = await cur.fetchall()
                            d = [i['txid'] for i in result]
                            # print('=================='+COIN_NAME+'===========')
                            # print(d)
                            # print('=================='+COIN_NAME+'===========')
                            for txes in get_transfers:
                                tx_in_block = txes['transactions']
                                for tx in tx_in_block:
                                    # Could be one block has two or more tx with different payment ID
                                    # add to balance only confirmation depth meet
                                    if height >= int(tx['blockIndex']) + wallet.get_confirm_depth(COIN_NAME) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) \
                                    and 'paymentId' in tx:
                                        if ('paymentId' in tx) and (tx['paymentId'] in list_balance_user):
                                            if tx['amount'] > 0:
                                                list_balance_user[tx['paymentId']] += tx['amount']
                                        elif ('paymentId' in tx) and (tx['paymentId'] not in list_balance_user):
                                            if tx['amount'] > 0:
                                                list_balance_user[tx['paymentId']] = tx['amount']
                                        try:
                                            if tx['transactionHash'] not in d:
                                                addresses = tx['transfers']
                                                address = ''
                                                for each_add in addresses:
                                                    if len(each_add['address']) > 0: address = each_add['address']
                                                    break
                                                    
                                                sql = """ INSERT IGNORE INTO cnoff_get_transfers (`coin_name`, `txid`, 
                                                `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                await cur.execute(sql, (COIN_NAME, tx['transactionHash'], tx['paymentId'], tx['blockIndex'], tx['timestamp'],
                                                                        tx['amount'], tx['fee'], wallet.get_decimal(COIN_NAME), address, int(time.time())))
                                                await conn.commit()
                                                # add to notification list also
                                                sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, 
                                                `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                                VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                                await cur.execute(sql, (COIN_NAME, tx['transactionHash'], tx['paymentId'], tx['blockIndex'],
                                                                        tx['amount'], tx['fee'], wallet.get_decimal(COIN_NAME)))
                                                await conn.commit()
                                        except pymysql.err.Warning as e:
                                            await logchanbot(traceback.format_exc())
                                        except Exception as e:
                                            await logchanbot(traceback.format_exc())
                                    elif height < int(tx['blockIndex']) + wallet.get_confirm_depth(COIN_NAME) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) \
                                    and 'paymentId' in tx:
                                        # add notify to redis and alert deposit. Can be clean later?
                                        if config.notify_new_tx.enable_new_no_confirm == 1:
                                            key_tx_new = 'TIPBOT:NEWTX:NOCONFIRM'
                                            key_tx_json = 'TIPBOT:NEWTX:' + tx['transactionHash']
                                            try:
                                                openRedis()
                                                if redis_conn and redis_conn.llen(key_tx_new) > 0:
                                                    list_new_tx = redis_conn.lrange(key_tx_new, 0, -1)
                                                    if list_new_tx and len(list_new_tx) > 0 and tx['transactionHash'] not in list_new_tx:
                                                        redis_conn.lpush(key_tx_new, tx['transactionHash'])
                                                        redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['transactionHash'], 'payment_id': tx['paymentId'], 'height': tx['blockIndex'],
                                                                                                'amount': tx['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                                elif redis_conn and redis_conn.llen(key_tx_new) == 0:
                                                    redis_conn.lpush(key_tx_new, tx['transactionHash'])
                                                    redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['transactionHash'], 'payment_id': tx['paymentId'], 'height': tx['blockIndex'],
                                                                                            'amount': tx['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                            except Exception as e:
                                                await logchanbot(traceback.format_exc())
                if list_balance_user and len(list_balance_user) > 0:
                    await openConnection()
                    async with pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            sql = """ SELECT coin_name, payment_id, SUM(amount) AS txIn FROM cnoff_get_transfers 
                                      WHERE coin_name = %s AND amount > 0 
                                      GROUP BY payment_id """
                            await cur.execute(sql, (COIN_NAME,))
                            result = await cur.fetchall()
                            timestamp = int(time.time())
                            list_update = []
                            if result and len(result) > 0:
                                for eachTxIn in result:
                                    list_update.append((eachTxIn['txIn'], timestamp, eachTxIn['payment_id']))
                                await cur.executemany(""" UPDATE cnoff_user_paymentid SET `actual_balance` = %s, `lastUpdate` = %s 
                                                          WHERE paymentid = %s """, list_update)
                                await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
    elif coin_family == "XMR":
        #print('SQL: Updating get_transfers '+COIN_NAME)
        get_transfers = await wallet.get_transfers_xmr(COIN_NAME)
        if get_transfers and len(get_transfers) >= 1:
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        sql = """ SELECT * FROM xmroff_get_transfers WHERE `coin_name` = %s """
                        await cur.execute(sql, (COIN_NAME,))
                        result = await cur.fetchall()
                        d = [i['txid'] for i in result]
                        # print('=================='+COIN_NAME+'===========')
                        # print(d)
                        # print('=================='+COIN_NAME+'===========')
                        list_balance_user = {}
                        for tx in get_transfers['in']:
                            # add to balance only confirmation depth meet
                            if height >= int(tx['height']) + wallet.get_confirm_depth(COIN_NAME) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) \
                            and 'payment_id' in tx:
                                if ('payment_id' in tx) and (tx['payment_id'] in list_balance_user):
                                    list_balance_user[tx['payment_id']] += tx['amount']
                                elif ('payment_id' in tx) and (tx['payment_id'] not in list_balance_user):
                                    list_balance_user[tx['payment_id']] = tx['amount']
                                try:
                                    if tx['txid'] not in d:
                                        sql = """ INSERT IGNORE INTO xmroff_get_transfers (`coin_name`, `in_out`, `txid`, 
                                        `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                        await cur.execute(sql, (COIN_NAME, tx['type'].upper(), tx['txid'], tx['payment_id'], tx['height'], tx['timestamp'],
                                                                tx['amount'], tx['fee'], wallet.get_decimal(COIN_NAME), tx['address'], int(time.time())))
                                        await conn.commit()
                                        # add to notification list also
                                        sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, 
                                        `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                        VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                        await cur.execute(sql, (COIN_NAME, tx['txid'], tx['payment_id'], tx['height'],
                                                                tx['amount'], tx['fee'], wallet.get_decimal(COIN_NAME)))
                                        await conn.commit()
                                except Exception as e:
                                    await logchanbot(traceback.format_exc())
                            elif height < int(tx['height']) + wallet.get_confirm_depth(COIN_NAME) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME) \
                            and 'payment_id' in tx:
                                # add notify to redis and alert deposit. Can be clean later?
                                if config.notify_new_tx.enable_new_no_confirm == 1:
                                    key_tx_new = 'TIPBOT:NEWTX:NOCONFIRM'
                                    key_tx_json = 'TIPBOT:NEWTX:' + tx['txid']
                                    try:
                                        openRedis()
                                        if redis_conn and redis_conn.llen(key_tx_new) > 0:
                                            list_new_tx = redis_conn.lrange(key_tx_new, 0, -1)
                                            if list_new_tx and len(list_new_tx) > 0 and tx['txid'] not in list_new_tx:
                                                redis_conn.lpush(key_tx_new, tx['txid'])
                                                redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['payment_id'], 'height': tx['height'],
                                                                                    'amount': tx['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                        elif redis_conn and redis_conn.llen(key_tx_new) == 0:
                                            redis_conn.lpush(key_tx_new, tx['txid'])
                                            redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['payment_id'], 'height': tx['height'],
                                                                                    'amount': tx['amount'], 'fee': tx['fee'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                    except Exception as e:
                                        await logchanbot(traceback.format_exc())
                        if len(list_balance_user) > 0:
                            list_update = []
                            timestamp = int(time.time())
                            for key, value in list_balance_user.items():
                                list_update.append((value, timestamp, key))
                            await cur.executemany(""" UPDATE xmroff_user_paymentid SET `actual_balance` = %s, `lastUpdate` = %s 
                                                      WHERE paymentid = %s """, list_update)
                            await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
    elif coin_family == "DOGE":
        #print('SQL: Updating get_transfers '+COIN_NAME)
        get_transfers = await wallet.doge_listtransactions(COIN_NAME)
        if get_transfers and len(get_transfers) >= 1:
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        sql = """ SELECT * FROM doge_get_transfers WHERE `coin_name` = %s AND `category` IN (%s, %s) """
                        await cur.execute(sql, (COIN_NAME, 'receive', 'send'))
                        result = await cur.fetchall()
                        d = [i['txid'] for i in result]
                        # print('=================='+COIN_NAME+'===========')
                        # print(d)
                        # print('=================='+COIN_NAME+'===========')
                        list_balance_user = {}
                        for tx in get_transfers:
                            # add to balance only confirmation depth meet
                            if wallet.get_confirm_depth(COIN_NAME) <= int(tx['confirmations']) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME):
                                if ('address' in tx) and (tx['address'] in list_balance_user) and (tx['amount'] > 0):
                                    list_balance_user[tx['address']] += tx['amount']
                                elif ('address' in tx) and (tx['address'] not in list_balance_user) and (tx['amount'] > 0):
                                    list_balance_user[tx['address']] = tx['amount']
                                try:
                                    if tx['txid'] not in d:
                                        if tx['category'] == "receive":
                                            sql = """ INSERT IGNORE INTO doge_get_transfers (`coin_name`, `txid`, `blockhash`, 
                                            `address`, `blocktime`, `amount`, `confirmations`, `category`, `time_insert`) 
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                            await cur.execute(sql, (COIN_NAME, tx['txid'], tx['blockhash'], tx['address'],
                                                                    tx['blocktime'], tx['amount'], tx['confirmations'], tx['category'], int(time.time())))
                                            await conn.commit()
                                        # add to notification list also, doge payment_id = address
                                        if (tx['amount'] > 0) and tx['category'] == 'receive':
                                            sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, 
                                            `payment_id`, `blockhash`, `amount`, `decimal`) 
                                            VALUES (%s, %s, %s, %s, %s, %s) """
                                            await cur.execute(sql, (COIN_NAME, tx['txid'], tx['address'], tx['blockhash'],
                                                                    tx['amount'], wallet.get_decimal(COIN_NAME)))
                                            await conn.commit()
                                except pymysql.err.Warning as e:
                                    await logchanbot(traceback.format_exc())
                                except Exception as e:
                                    await logchanbot(traceback.format_exc())
                            if wallet.get_confirm_depth(COIN_NAME) > int(tx['confirmations']) and tx['amount'] >= wallet.get_min_deposit_amount(COIN_NAME):
                                # add notify to redis and alert deposit. Can be clean later?
                                if config.notify_new_tx.enable_new_no_confirm == 1:
                                    key_tx_new = 'TIPBOT:NEWTX:NOCONFIRM'
                                    key_tx_json = 'TIPBOT:NEWTX:' + tx['txid']
                                    try:
                                        openRedis()
                                        if redis_conn and redis_conn.llen(key_tx_new) > 0:
                                            list_new_tx = redis_conn.lrange(key_tx_new, 0, -1)
                                            if list_new_tx and len(list_new_tx) > 0 and tx['txid'] not in list_new_tx:
                                                redis_conn.lpush(key_tx_new, tx['txid'])
                                                redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['address'], 'blockhash': tx['blockhash'],
                                                                                        'amount': tx['amount'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                        elif redis_conn and redis_conn.llen(key_tx_new) == 0:
                                            redis_conn.lpush(key_tx_new, tx['txid'])
                                            redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['address'], 'blockhash': tx['blockhash'],
                                                                                    'amount': tx['amount'], 'decimal': wallet.get_decimal(COIN_NAME)}), ex=86400)
                                    except Exception as e:
                                        await logchanbot(traceback.format_exc())
                        if len(list_balance_user) > 0:
                            list_update = []
                            timestamp = int(time.time())
                            for key, value in list_balance_user.items():
                                list_update.append((value, timestamp, key))
                            await cur.executemany(""" UPDATE doge_user SET `actual_balance` = %s, `lastUpdate` = %s 
                                                      WHERE balance_wallet_address = %s """, list_update)
                            await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())


async def sql_credit(user_from: str, to_user: str, amount: float, coin: str, reason: str):
    global pool
    COIN_NAME = coin.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO credit_balance (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `credit_date`, `reason`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_from, to_user, amount, wallet.get_decimal(COIN_NAME), int(time.time()), reason,))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_update_some_balances(wallet_addresses: List[str], coin: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")

    updateTime = int(time.time())
    if coin_family in ["TRTL", "BCN"]:
        print('SQL: Updating some wallet balances '+COIN_NAME)
        if COIN_NAME in WALLET_API_COIN:
            balances = await walletapi.walletapi_get_some_balances(wallet_addresses, COIN_NAME)
        else:
            balances = await wallet.get_some_balances(wallet_addresses, COIN_NAME)
        try:
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    values_str = []
                    for details in balances:
                        address = details['address']
                        actual_balance = details['unlocked']
                        locked_balance = details['locked']
                        decimal = wallet.get_decimal(COIN_NAME)
                        values_str.append(f"('{COIN_NAME}', '{address}', {actual_balance}, {locked_balance}, {decimal}, {updateTime})\n")
                    values_sql = "VALUES " + ",".join(values_str)
                    sql = """ INSERT INTO cn_walletapi (`coin_name`, `balance_wallet_address`, `actual_balance`, 
                              `locked_balance`, `decimal`, `lastUpdate`) """+values_sql+""" 
                              ON DUPLICATE KEY UPDATE 
                              `actual_balance` = VALUES(`actual_balance`),
                              `locked_balance` = VALUES(`locked_balance`),
                              `decimal` = VALUES(`decimal`),
                              `lastUpdate` = VALUES(`lastUpdate`)
                              """
                    await cur.execute(sql,)
                    await conn.commit()
        except Exception as e:
            await logchanbot(traceback.format_exc())
    return False


async def sql_get_alluser_balance(coin: str, filename: str):
    global pool
    COIN_NAME = coin.upper()
    if COIN_NAME in ENABLE_COIN:
        try:
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    sql = """ SELECT user_id, balance_wallet_address, user_wallet_address, user_server FROM cn_user 
                              WHERE `coin_name` = %s """
                    await cur.execute(sql, (COIN_NAME,))
                    result = await cur.fetchall()
                    write_csv_dumpinfo = open(filename, "w")
                    for item in result:
                        getBalance = await sql_get_userwallet(item['user_id'], COIN_NAME)
                        if getBalance:
                            user_balance_total = getBalance['actual_balance'] + getBalance['locked_balance']
                            write_csv_dumpinfo.write(str(item['user_id']) + ';' + wallet.num_format_coin(user_balance_total, COIN_NAME) + ';' + item['balance_wallet_address'] + '\n')
                    write_csv_dumpinfo.close()
                    return True
        except Exception as e:
            await logchanbot(traceback.format_exc())
            return False
    return False


async def sql_register_user(userID, coin: str, user_server: str = 'DISCORD', chat_id: int = 0):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    if user_server == "TELEGRAM" and chat_id == 0:
        return
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if coin_family in ["TRTL", "BCN"]:
                    sql = """ SELECT user_id, int_address, user_wallet_address, user_server FROM cnoff_user_paymentid 
                              WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (userID, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "XMR":
                    sql = """ SELECT * FROM xmroff_user_paymentid 
                              WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "DOGE":
                    sql = """ SELECT * FROM doge_user WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "NANO":
                    sql = """ SELECT * FROM nano_user WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                if result is None:
                    balance_address = None
                    main_address = None
                    if coin_family in ["TRTL", "BCN"]:
                        main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
                        balance_address = {}
                        balance_address['payment_id'] = addressvalidation.paymentid()
                        balance_address['integrated_address'] = addressvalidation.make_integrated_cn(main_address, COIN_NAME, balance_address['payment_id'])['integrated_address']
                    elif coin_family == "XMR":
                        main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
                        balance_address = await wallet.make_integrated_address_xmr(main_address, COIN_NAME)
                    elif coin_family == "DOGE":
                        balance_address = await wallet.doge_register(str(userID), COIN_NAME, user_server)
                    elif coin_family == "NANO":
                        # No need ID
                        balance_address = await wallet.nano_register(COIN_NAME, user_server)
                    if balance_address is None:
                        print('Internal error during call register wallet-api')
                        return
                    else:
                        if coin_family in ["TRTL", "BCN"]:
                            sql = """ INSERT INTO cnoff_user_paymentid (`coin_name`, `user_id`, `main_address`, `paymentid`, 
                                  `int_address`, `paymentid_ts`, `user_server`, `chat_id`) 
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), main_address, balance_address['payment_id'], 
                                                    balance_address['integrated_address'], int(time.time()), user_server, chat_id))
                            await conn.commit()
                        elif coin_family == "XMR":
                            sql = """ INSERT INTO xmroff_user_paymentid (`coin_name`, `user_id`, `main_address`, `paymentid`, 
                                      `int_address`, `paymentid_ts`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), main_address, balance_address['payment_id'], 
                                                    balance_address['integrated_address'], int(time.time()), user_server))
                            await conn.commit()
                        elif coin_family == "DOGE":
                            sql = """ INSERT INTO doge_user (`coin_name`, `user_id`, `balance_wallet_address`, `address_ts`, 
                                      `privateKey`, `user_server`, `chat_id`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), balance_address['address'], int(time.time()), 
                                                    encrypt_string(balance_address['privateKey']), user_server, chat_id))
                            await conn.commit()
                        elif coin_family == "NANO":
                            sql = """ INSERT INTO nano_user (`coin_name`, `user_id`, `balance_wallet_address`, `address_ts`, 
                                      `user_server`, `chat_id`) 
                                      VALUES (%s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), balance_address['address'], int(time.time()), 
                                                    user_server, chat_id))
                            await conn.commit()
                    return balance_address
                else:
                    return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_update_user(userID, user_wallet_address, coin: str, user_server: str = 'DISCORD'):
    global redis_conn, pool
    COIN_NAME = coin.upper()
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    # Check if exist in redis
    try:
        openRedis()
        if redis_conn and redis_conn.exists(f'TIPBOT:WALLET_{str(userID)}_{COIN_NAME}'):
            redis_conn.delete(f'TIPBOT:WALLET_{str(userID)}_{COIN_NAME}')
    except Exception as e:
        await logchanbot(traceback.format_exc())

    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if coin_family in ["TRTL", "BCN"]:
                    sql = """ UPDATE cnoff_user_paymentid SET user_wallet_address=%s WHERE user_id=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """               
                    await cur.execute(sql, (user_wallet_address, str(userID), COIN_NAME, user_server))
                    await conn.commit()
                elif coin_family == "XMR":
                    sql = """ UPDATE xmroff_user_paymentid SET user_wallet_address=%s WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """               
                    await cur.execute(sql, (user_wallet_address, str(userID), COIN_NAME, user_server))
                    await conn.commit()
                elif coin_family == "DOGE":
                    sql = """ UPDATE doge_user SET user_wallet_address=%s WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """               
                    await cur.execute(sql, (user_wallet_address, str(userID), COIN_NAME, user_server))
                    await conn.commit()
                elif coin_family == "NANO":
                    sql = """ UPDATE nano_user SET user_wallet_address=%s WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """               
                    await cur.execute(sql, (user_wallet_address, str(userID), COIN_NAME, user_server))
                    await conn.commit()
                return user_wallet_address  # return userwallet
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_userwallet(userID, coin: str, user_server: str = 'DISCORD'):
    global pool, redis_conn, redis_expired
    COIN_NAME = coin.upper()
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    # Check if exist in redis
    try:
        openRedis()
        if redis_conn and redis_conn.exists(f'TIPBOT:WALLET_{str(userID)}_{COIN_NAME}'):
            return json.loads(redis_conn.get(f'TIPBOT:WALLET_{str(userID)}_{COIN_NAME}').decode())
    except Exception as e:
        await logchanbot(traceback.format_exc())

    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = None
                if coin_family in ["TRTL", "BCN"]:
                    sql = """ SELECT * FROM cnoff_user_paymentid WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "XMR":
                    sql = """ SELECT * FROM xmroff_user_paymentid WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "DOGE":
                    sql = """ SELECT user_id, balance_wallet_address, user_wallet_address, address_ts, lastUpdate, chat_id 
                              FROM doge_user WHERE `user_id`=%s AND `coin_name`=%s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "NANO":
                    sql = """ SELECT * FROM nano_user WHERE `user_id`=%s AND `coin_name`=%s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                    result = await cur.fetchone()
                if result:
                    userwallet = result
                    if coin_family == "XMR":
                        userwallet['balance_wallet_address'] = userwallet['int_address']
                    elif coin_family in ["TRTL", "BCN"]:
                        userwallet['balance_wallet_address'] = userwallet['int_address']
                        userwallet['actual_balance'] = int(result['actual_balance'])
                        userwallet['locked_balance'] = int(result['locked_balance'])
                        userwallet['lastUpdate'] = int(result['lastUpdate'])
                    elif coin_family == "DOGE":
                        async with conn.cursor() as cur:
                            sql = """ SELECT * FROM doge_user WHERE `user_id`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                            await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                            result = await cur.fetchone()
                            if result:
                                userwallet['actual_balance'] = result['actual_balance']
                                userwallet['locked_balance'] = 0 # There shall not be locked balance
                                userwallet['lastUpdate'] = result['lastUpdate']
                    elif coin_family == "NANO":
                        return userwallet
                    if result['lastUpdate'] == 0 and (coin_family in ["TRTL", "BCN"] or coin_family == "XMR"):
                        userwallet['lastUpdate'] = result['paymentid_ts']
                    return userwallet
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_get_countLastTip(userID, lastDuration: int):
    global pool
    lapDuration = int(time.time()) - lastDuration
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ (SELECT `coin_name`, `from_user`,`amount`,`date` FROM cn_tip WHERE `from_user` = %s AND `date`>%s )
                          UNION
                          (SELECT `coin_name`, `from_user`,`amount_total`,`date` FROM cn_tipall WHERE `from_user` = %s AND `date`>%s )
                          UNION
                          (SELECT `coin_name`, `from_user`,`amount`,`date` FROM cn_send WHERE `from_user` = %s AND `date`>%s )
                          UNION
                          (SELECT `coin_name`, `user_id`,`amount`,`date` FROM cn_withdraw WHERE `user_id` = %s AND `date`>%s )
                          UNION
                          (SELECT `coin_name`, `from_user`,`amount`,`date` FROM cn_donate WHERE `from_user` = %s AND `date`>%s )
                          ORDER BY `date` DESC LIMIT 10 """
                await cur.execute(sql, (str(userID), lapDuration, str(userID), lapDuration, str(userID), lapDuration,
                                        str(userID), lapDuration, str(userID), lapDuration,))
                result = await cur.fetchall()

                # Can be tipall or tip many, let's count all
                sql = """ SELECT `coin_name`, `from_userid`,`amount`,`date` FROM cnoff_mv_tx WHERE `from_userid` = %s AND `date`>%s 
                          ORDER BY `date` DESC LIMIT 100 """
                await cur.execute(sql, (str(userID), lapDuration,))
                result2 = await cur.fetchall()

                # doge table
                sql = """ SELECT `coin_name`, `from_userid`,`amount`,`date` FROM doge_mv_tx WHERE `from_userid` = %s AND `date`>%s 
                          ORDER BY `date` DESC LIMIT 100 """
                await cur.execute(sql, (str(userID), lapDuration,))
                result3 = await cur.fetchall()

                if (result is None) and (result2 is None) and (result3 is None):
                    return 0
                else:
                    return (len(result) if result else 0) + (len(result2) if result2 else 0) + (len(result3) if result3 else 0)
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_send_tip(user_from: str, user_to: str, amount: int, tiptype: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    user_from_wallet = None
    user_to_wallet = None
    address_to = None
    if coin_family in ["TRTL", "BCN", "XMR"]:
        user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
        user_to_wallet = await sql_get_userwallet(user_to, COIN_NAME, user_server)
        if user_to_wallet and user_to_wallet['forwardtip'] == "ON" and user_to_wallet['user_wallet_address']:
            address_to = user_to_wallet['user_wallet_address']
        else:
            address_to = user_to_wallet['balance_wallet_address']
    if all(v is not None for v in [user_from_wallet['balance_wallet_address'], address_to]):
        if coin_family in ["TRTL", "BCN"]:
            # Move balance
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        sql = """ INSERT INTO cnoff_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`, `user_server`) 
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                        await cur.execute(sql, (COIN_NAME, user_from, user_to, amount, wallet.get_decimal(COIN_NAME), tiptype.upper(), int(time.time()), user_server,))
                        await conn.commit()
                        return {'transactionHash': 'NONE', 'fee': 0}
            except Exception as e:
                await logchanbot(traceback.format_exc())
    return False


async def sql_send_tipall(user_from: str, user_tos, amount: int, amount_div: int, user_ids, tiptype: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")

    if tiptype.upper() not in ["TIPS", "TIPALL", "FREETIP", "FREETIPS"]:
        return None

    user_from_wallet = None
    if coin_family in ["TRTL", "BCN", "XMR"]:
        user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
    if user_from_wallet['balance_wallet_address']:
        if coin_family in ["TRTL", "BCN"]:
            # Move offchain
            values_str = []
            currentTs = int(time.time())
            for item in user_ids:
                values_str.append(f"('{COIN_NAME}', '{user_from}', '{item}', {amount_div}, {wallet.get_decimal(COIN_NAME)}, '{tiptype.upper()}', {currentTs})\n")
            values_sql = "VALUES " + ",".join(values_str)
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        sql = """ INSERT INTO cnoff_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`) 
                                  """+values_sql+""" """
                        await cur.execute(sql,)
                        await conn.commit()
                        return {'transactionHash': 'NONE', 'fee': 0}
            except Exception as e:
                await logchanbot(traceback.format_exc())
                print(f"SQL:\n{sql}\n")
    return False


async def sql_send_tip_Ex(user_from: str, address_to: str, amount: int, coin: str, user_server: str = 'DISCORD'):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    user_from_wallet = None
    if coin_family in ["TRTL", "BCN", "XMR"]:
        user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
    if user_from_wallet['balance_wallet_address']:
        tx_hash = None
        if coin_family in ["TRTL", "BCN"]:
            # send from wallet and store in cnoff_external_tx
            main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
            if COIN_NAME in WALLET_API_COIN:
                tx_hash = await walletapi.walletapi_send_transaction(main_address, address_to, 
                                                                     amount, COIN_NAME)

            else:
                tx_hash = await wallet.send_transaction(main_address, address_to, 
                                                        amount, COIN_NAME)
        elif coin_family == "XMR":
            tx_hash = await wallet.send_transaction(user_from_wallet['balance_wallet_address'], address_to, 
                                                    amount, COIN_NAME, user_from_wallet['account_index'])
        if tx_hash:
            updateTime = int(time.time())
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        timestamp = int(time.time())
                        if coin_family in ["TRTL", "BCN"]:
                            fee = 0
                            if COIN_NAME not in FEE_PER_BYTE_COIN:
                                fee = wallet.get_tx_fee(COIN_NAME)
                            else:
                                fee = tx_hash['fee']
                            sql = """ INSERT INTO cnoff_external_tx (`coin_name`, `user_id`, `to_address`, `amount`, `decimal`, `date`, 
                                      `tx_hash`, `fee`, `user_server`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, user_from, address_to, amount, wallet.get_decimal(COIN_NAME), timestamp, 
                                                    tx_hash['transactionHash'], fee, user_server))
                            await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
            return tx_hash
    return False


async def sql_send_tip_Ex_id(user_from: str, address_to: str, amount: int, paymentid, coin: str, user_server: str = 'DISCORD'):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
    if 'balance_wallet_address' in user_from_wallet:
        tx_hash = None
        if coin_family in ["TRTL", "BCN"]:
            # send from wallet and store in cnoff_external_tx
            main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
            if COIN_NAME in WALLET_API_COIN:
                tx_hash = await walletapi.walletapi_send_transaction_id(main_address, address_to,
                                                                        amount, paymentid, COIN_NAME)
            else:
                tx_hash = await wallet.send_transaction_id(main_address, address_to,
                                                           amount, paymentid, COIN_NAME)
        if tx_hash:
            updateTime = int(time.time())
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        timestamp = int(time.time())
                        if coin_family in ["TRTL", "BCN"]:
                            fee = 0
                            if COIN_NAME not in FEE_PER_BYTE_COIN:
                                fee = wallet.get_tx_fee(COIN_NAME)
                            else:
                                fee = tx_hash['fee']
                            sql = """ INSERT INTO cnoff_external_tx (`coin_name`, `user_id`, `to_address`, `amount`, `decimal`, `date`, 
                                      `tx_hash`, `paymentid`, `fee`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, user_from, address_to, amount, wallet.get_decimal(COIN_NAME), 
                                                    timestamp, tx_hash['transactionHash'], paymentid, fee, user_server))
                            await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
            return tx_hash
    return False


async def sql_withdraw(user_from: str, amount: int, coin: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    tx_hash = None
    user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
    if all(v is not None for v in [user_from_wallet['balance_wallet_address'], user_from_wallet['user_wallet_address']]):
        if coin_family in ["TRTL", "BCN"]:
            # send from wallet and store in cnoff_external_tx
            main_address = getattr(getattr(config,"daemon"+COIN_NAME),"MainAddress")
            try:
                if COIN_NAME in WALLET_API_COIN:
                    tx_hash = await walletapi.walletapi_send_transaction(main_address,
                                                                         user_from_wallet['user_wallet_address'], amount, COIN_NAME)

                else:
                    tx_hash = await wallet.send_transaction(main_address,
                                                            user_from_wallet['user_wallet_address'], amount, COIN_NAME)
            except Exception as e:
                await logchanbot(traceback.format_exc())
        elif coin_family == "XMR":
            tx_hash = await wallet.send_transaction(user_from_wallet['balance_wallet_address'],
                                                    user_from_wallet['user_wallet_address'], amount, COIN_NAME, user_from_wallet['account_index'])
        if tx_hash:
            updateTime = int(time.time())
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        timestamp = int(time.time())
                        if coin_family in ["TRTL", "BCN"]:
                            sql = """ INSERT INTO cnoff_external_tx (`coin_name`, `user_id`, `to_address`, `amount`, 
                                      `decimal`, `date`, `tx_hash`, `fee`, `user_server`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                            fee = 0
                            if COIN_NAME not in FEE_PER_BYTE_COIN:
                                fee = wallet.get_tx_fee(COIN_NAME)
                            else:
                                fee = tx_hash['fee']
                            await cur.execute(sql, (COIN_NAME, user_from, user_from_wallet['user_wallet_address'], amount, wallet.get_decimal(COIN_NAME), timestamp, tx_hash['transactionHash'], fee, user_server))
                            await conn.commit()
                        elif coin_family == "XMR":
                            sql = """ INSERT INTO xmroff_withdraw (`coin_name`, `user_id`, `to_address`, `amount`, 
                                      `fee`, `date`, `tx_hash`, `tx_key`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, user_from, user_from_wallet['user_wallet_address'], amount, tx_hash['fee'], timestamp, tx_hash['tx_hash'], tx_hash['tx_key'],))
                            await conn.commit()
            except Exception as e:
                await logchanbot(traceback.format_exc())
        return tx_hash
    else:
        return None


async def sql_donate(user_from: str, address_to: str, amount: int, coin: str, user_server: str = 'DISCORD') -> str:
    global pool
    user_server = user_server.upper()
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")

    user_from_wallet = await sql_get_userwallet(user_from, COIN_NAME, user_server)
    if all(v is not None for v in [user_from_wallet['balance_wallet_address'], address_to]):
        if coin_family in ["TRTL", "BCN"]:
            # Move balance
            try:
                await openConnection()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        sql = """ INSERT INTO cnoff_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`, `user_server`) 
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                        await cur.execute(sql, (COIN_NAME, user_from, wallet.get_donate_address(COIN_NAME), amount, 
                                                wallet.get_decimal(COIN_NAME), 'DONATE', int(time.time()), user_server))
                        await conn.commit()
                        return {'transactionHash': 'NONE', 'fee': 0}
            except Exception as e:
                await logchanbot(traceback.format_exc())
    else:
        return None


async def sql_get_donate_list():
    global pool
    donate_list = {}
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # TRTL fam
                for coin in ENABLE_COIN:
                    sql = """ SELECT SUM(amount) AS donate FROM cn_donate WHERE `coin_name`= %s """
                    await cur.execute(sql, (coin.upper()))
                    result = await cur.fetchone()
                    if result['donate'] is None:
                        donate_list.update({coin: 0})
                    else:
                        donate_list.update({coin: float(result['donate'])})
                # TRTL fam but in cnoff_mv_tx table
                for coin in ENABLE_COIN:
                    sql = """ SELECT SUM(amount) AS donate FROM cnoff_mv_tx WHERE `coin_name`= %s AND `type`=%s """
                    await cur.execute(sql, (coin.upper(), 'DONATE'))
                    result = await cur.fetchone()
                    if result and result['donate'] and result['donate'] > 0:
                        donate_list[coin] += float(result['donate'])
                # DOGE fam
                for coin in ENABLE_COIN_DOGE:
                    sql = """ SELECT SUM(amount) AS donate FROM doge_mv_tx WHERE `type`='DONATE' AND `to_userid`= %s AND `coin_name`= %s """
                    await cur.execute(sql, ((wallet.get_donate_address(coin), coin.upper())))
                    result = await cur.fetchone()
                    if result['donate'] is None:
                        donate_list.update({coin: 0})
                    else:
                       donate_list.update({coin: float(result['donate'])})
                # XTOR
                coin = "XTOR"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # LOKI
                coin = "LOKI"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # XMR
                coin = "XMR"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # WOW
                coin = "WOW"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # XOL
                coin = "XOL"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # MSR
                coin = "MSR"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # XAM
                coin = "XAM"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # BLOG
                coin = "BLOG"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # UPX
                coin = "UPX"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
                # XWP
                coin = "XWP"
                sql = """ SELECT SUM(amount) AS donate FROM xmroff_mv_tx as donate WHERE `type`='DONATE' AND `to_userid`= %s """
                await cur.execute(sql, (wallet.get_donate_address(coin)))
                result = await cur.fetchone()
                if result['donate'] is None:
                    donate_list.update({coin: 0})
                else:
                    donate_list.update({coin: float(result['donate'])})
            return donate_list
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_send_to_voucher(user_id: str, user_name: str, message_creating: str, amount: float, reserved_fee: float, comment: str, secret_string: str, voucher_image_name: str, coin: str, user_server: str='DISCORD'):
    global pool
    COIN_NAME = coin.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO cn_voucher (`coin_name`, `user_id`, `user_name`, `message_creating`, `amount`, 
                          `decimal`, `reserved_fee`, `date_create`, `comment`, `secret_string`, `voucher_image_name`, `user_server`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_id, user_name, message_creating, amount, wallet.get_decimal(COIN_NAME), reserved_fee, 
                                        int(time.time()), comment, secret_string, voucher_image_name, user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_voucher_get_user(user_id: str, user_server: str='DISCORD', last: int=10, already_claimed: str='YESNO'):
    global pool
    user_server = user_server.upper()
    already_claimed = already_claimed.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if already_claimed == 'YESNO':
                    sql = """ SELECT * FROM cn_voucher WHERE `user_id`=%s AND `user_server`=%s 
                              ORDER BY `date_create` DESC LIMIT """ + str(last)+ """ """
                    await cur.execute(sql, (user_id, user_server,))
                    result = await cur.fetchall()
                    return result
                elif already_claimed == 'YES' or already_claimed == 'NO':
                    sql = """ SELECT * FROM cn_voucher WHERE `user_id`=%s AND `user_server`=%s AND `already_claimed`=%s
                              ORDER BY `date_create` DESC LIMIT """ + str(last)+ """ """
                    await cur.execute(sql, (user_id, user_server, already_claimed))
                    result = await cur.fetchall()
                    return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_faucet_add(claimed_user: str, claimed_server: str, coin_name: str, claimed_amount: float, decimal: int, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO discord_faucet (`claimed_user`, `coin_name`, `claimed_amount`, 
                          `decimal`, `claimed_at`, `claimed_server`, `user_server`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (claimed_user, coin_name, claimed_amount, decimal, 
                                        int(time.time()), claimed_server, user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_faucet_checkuser(userID: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    list_roach = await sql_roach_get_by_id(userID, user_server)
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if list_roach:
                    roach_sql = "(" + ",".join(list_roach) + ")"
                    sql = """ SELECT * FROM discord_faucet WHERE claimed_user IN """+roach_sql+""" AND `user_server`=%s 
                              ORDER BY claimed_at DESC LIMIT 1"""
                    await cur.execute(sql, (user_server,))
                else:
                    sql = """ SELECT * FROM discord_faucet WHERE `claimed_user` = %s AND `user_server`=%s 
                              ORDER BY claimed_at DESC LIMIT 1"""
                    await cur.execute(sql, (userID, (user_server,)))
                result = await cur.fetchone()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_faucet_count_user(userID: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT COUNT(*) FROM discord_faucet WHERE claimed_user = %s AND `user_server`=%s """
                await cur.execute(sql, (userID, user_server))
                result = await cur.fetchone()
                return int(result['COUNT(*)']) if 'COUNT(*)' in result else 0
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_faucet_count_all():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT COUNT(*) FROM discord_faucet """
                await cur.execute(sql,)
                result = await cur.fetchone()
                return int(result['COUNT(*)']) if 'COUNT(*)' in result else 0
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_faucet_sum_count_claimed(coin: str):
    COIN_NAME = coin.upper()
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT SUM(claimed_amount) as claimed, COUNT(claimed_amount) as count FROM discord_faucet
                          WHERE `coin_name`=%s """
                await cur.execute(sql, (COIN_NAME))
                result = await cur.fetchone()
                # {'claimed_amount': xxx, 'count': xxx}
                # print(result)
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_game_count_user(userID: str, lastDuration: int, user_server: str = 'DISCORD', free: bool=False):
    global pool
    lapDuration = int(time.time()) - lastDuration
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if free == False:
                    sql = """ SELECT COUNT(*) FROM discord_game WHERE `played_user` = %s AND `user_server`=%s 
                              AND `played_at`>%s """
                else:
                    sql = """ SELECT COUNT(*) FROM discord_game_free WHERE `played_user` = %s AND `user_server`=%s 
                              AND `played_at`>%s """
                await cur.execute(sql, (userID, user_server, lapDuration))
                result = await cur.fetchone()
                return int(result['COUNT(*)']) if 'COUNT(*)' in result else 0
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_game_add(game_result: str, played_user: str, coin_name: str, win_lose: str, won_amount: float, decimal: int, \
played_server: str, game_type: str, duration: int=0, user_server: str = 'DISCORD'):
    global pool
    game_result = game_result.replace("\t", "")
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO discord_game (`played_user`, `coin_name`, `win_lose`, 
                          `won_amount`, `decimal`, `played_server`, `played_at`, `game_type`, `user_server`, `game_result`, `duration`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (played_user, coin_name, win_lose, won_amount, decimal, played_server, 
                                        int(time.time()), game_type, user_server, game_result, duration))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_game_free_add(game_result: str, played_user: str, win_lose: str, played_server: str, game_type: str, duration: int=0, user_server: str = 'DISCORD'):
    global pool
    game_result = game_result.replace("\t", "")
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO discord_game_free (`played_user`, `win_lose`, `played_server`, `played_at`, `game_type`, `user_server`, `game_result`, `duration`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (played_user, win_lose, played_server, int(time.time()), game_type, user_server, game_result, duration))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_game_stat():
    global pool
    stat = {}
    GAME_COIN = config.game.coin_game.split(",")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_game """
                await cur.execute(sql,)
                result_game = await cur.fetchall()
                if result_game and len(result_game) > 0:
                    stat['paid_play'] = len(result_game)
                    # https://stackoverflow.com/questions/21518271/how-to-sum-values-of-the-same-key-in-a-dictionary
                    stat['paid_hangman_play'] = sum(d.get('HANGMAN', 0) for d in result_game)
                    stat['paid_bagel_play'] = sum(d.get('BAGEL', 0) for d in result_game)
                    stat['paid_slot_play'] = sum(d.get('SLOT', 0) for d in result_game)
                    for each in GAME_COIN:
                        stat[each] = sum(d.get('won_amount', 0) for d in result_game if d['coin_name'] == each)
                sql = """ SELECT * FROM discord_game_free """
                await cur.execute(sql,)
                result_game_free = await cur.fetchall()
                if result_game_free and len(result_game_free) > 0:
                    stat['free_play'] = len(result_game_free)
                    stat['free_hangman_play'] = sum(d.get('HANGMAN', 0) for d in result_game_free)
                    stat['free_bagel_play'] = sum(d.get('BAGEL', 0) for d in result_game_free)
                    stat['free_slot_play'] = sum(d.get('SLOT', 0) for d in result_game_free)
            return stat
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_count_tx_all():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT COUNT(*) FROM cnoff_external_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                cnoff_external_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM cnoff_mv_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                cnoff_mv_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM cn_tip """
                await cur.execute(sql,)
                result = await cur.fetchone()
                cn_tip = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM cn_send """
                await cur.execute(sql,)
                result = await cur.fetchone()
                cn_send = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM cn_withdraw """
                await cur.execute(sql,)
                result = await cur.fetchone()
                cn_withdraw = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM doge_external_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                doge_external_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM doge_mv_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                doge_mv_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM xmroff_external_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                xmroff_external_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0

                sql = """ SELECT COUNT(*) FROM xmroff_mv_tx """
                await cur.execute(sql,)
                result = await cur.fetchone()
                xmroff_mv_tx = int(result['COUNT(*)']) if 'COUNT(*)' in result else 0
                
                on_chain = cnoff_external_tx + cn_tip + cn_send + cn_withdraw + doge_external_tx + xmroff_external_tx
                off_chain = cnoff_mv_tx + doge_mv_tx + xmroff_mv_tx
                return {'on_chain': on_chain, 'off_chain': off_chain, 'total': on_chain+off_chain}
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_tag_by_server(server_id: str, tag_id: str = None):
    global pool, redis_pool, redis_conn, redis_expired
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if tag_id is None: 
                    sql = """ SELECT * FROM discord_tag WHERE tag_serverid = %s """
                    await cur.execute(sql, (server_id,))
                    result = await cur.fetchall()
                    tag_list = result
                    return tag_list
                else:
                    # Check if exist in redis
                    try:
                        openRedis()
                        if redis_conn and redis_conn.exists(f'TIPBOT:TAG_{str(server_id)}_{tag_id}'):
                            sql = """ UPDATE discord_tag SET num_trigger=num_trigger+1 WHERE tag_serverid = %s AND tag_id=%s """
                            await cur.execute(sql, (server_id, tag_id,))
                            await conn.commit()
                            return json.loads(redis_conn.get(f'TIPBOT:TAG_{str(server_id)}_{tag_id}'))
                        else:
                            sql = """ SELECT `tag_id`, `tag_desc`, `date_added`, `tag_serverid`, `added_byname`, 
                                      `added_byuid`, `num_trigger` FROM discord_tag WHERE tag_serverid = %s AND tag_id=%s """
                            await cur.execute(sql, (server_id, tag_id,))
                            result = await cur.fetchone()
                            if result:
                                redis_conn.set(f'TIPBOT:TAG_{str(server_id)}_{tag_id}', json.dumps(result), ex=redis_expired)
                                return json.loads(redis_conn.get(f'TIPBOT:TAG_{str(server_id)}_{tag_id}'))
                    except Exception as e:
                        await logchanbot(traceback.format_exc())
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_tag_by_server_add(server_id: str, tag_id: str, tag_desc: str, added_byname: str, added_byuid: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT COUNT(*) FROM discord_tag WHERE tag_serverid=%s """
                await cur.execute(sql, (server_id,))
                counting = await cur.fetchone()
                if counting:
                    if counting['COUNT(*)'] > 50:
                        return None
                sql = """ SELECT `tag_id`, `tag_desc`, `date_added`, `tag_serverid`, `added_byname`, `added_byuid`, 
                          `num_trigger` 
                          FROM discord_tag WHERE tag_serverid = %s AND tag_id=%s """
                await cur.execute(sql, (server_id, tag_id.upper(),))
                result = await cur.fetchone()
                if result is None:
                    sql = """ INSERT INTO discord_tag (`tag_id`, `tag_desc`, `date_added`, `tag_serverid`, 
                              `added_byname`, `added_byuid`) 
                              VALUES (%s, %s, %s, %s, %s, %s) """
                    await cur.execute(sql, (tag_id.upper(), tag_desc, int(time.time()), server_id, added_byname, added_byuid,))
                    await conn.commit()
                    return tag_id.upper()
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_tag_by_server_del(server_id: str, tag_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT `tag_id`, `tag_desc`, `date_added`, `tag_serverid`, `added_byname`, 
                          `added_byuid`, `num_trigger` 
                          FROM discord_tag WHERE tag_serverid = %s AND tag_id=%s """
                await cur.execute(sql, (server_id, tag_id.upper(),))
                result = await cur.fetchone()
                if result:
                    sql = """ DELETE FROM discord_tag WHERE `tag_id`=%s AND `tag_serverid`=%s """
                    await cur.execute(sql, (tag_id.upper(), server_id,))
                    await conn.commit()
                    # Check if exist in redis
                    try:
                        openRedis()
                        if redis_conn and redis_conn.exists(f'TIPBOT:TAG_{str(server_id)}_{tag_id}'):
                            redis_conn.delete(f'TIPBOT:TAG_{str(server_id)}_{tag_id}')
                    except Exception as e:
                        await logchanbot(traceback.format_exc())
                    return tag_id.upper()
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_itag_by_server(server_id: str, tag_id: str = None):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if tag_id is None: 
                    sql = """ SELECT * FROM discord_itag WHERE itag_serverid = %s """
                    await cur.execute(sql, (server_id,))
                    result = await cur.fetchall()
                    tag_list = result
                    return tag_list
                else:
                    sql = """ SELECT * FROM discord_itag WHERE itag_serverid = %s AND itag_id=%s """
                    await cur.execute(sql, (server_id, tag_id,))
                    result = await cur.fetchone()
                    if result:
                        tag = result
                        sql = """ UPDATE discord_itag SET num_trigger=num_trigger+1 WHERE itag_serverid = %s AND itag_id=%s """
                        await cur.execute(sql, (server_id, tag_id,))
                        await conn.commit()
                        return tag
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_itag_by_server_add(server_id: str, tag_id: str, added_byname: str, added_byuid: str, orig_name: str, stored_name: str, fsize: int):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT COUNT(*) FROM discord_itag WHERE itag_serverid=%s """
                await cur.execute(sql, (server_id,))
                counting = await cur.fetchone()
                if counting:
                    if counting['COUNT(*)'] > config.itag.max_per_server:
                        return None
                sql = """ SELECT * FROM discord_itag WHERE itag_serverid = %s AND itag_id=%s """
                await cur.execute(sql, (server_id, tag_id.upper(),))
                result = await cur.fetchone()
                if result is None:
                    sql = """ INSERT INTO discord_itag (`itag_id`, `date_added`, `itag_serverid`, 
                              `added_byname`, `added_byuid`, `original_name`, `stored_name`, `size`) 
                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                    await cur.execute(sql, (tag_id.upper(), int(time.time()), server_id, added_byname, added_byuid, orig_name, stored_name, fsize))
                    await conn.commit()
                    return tag_id.upper()
                else:
                    return None
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_itag_by_server_del(server_id: str, tag_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_itag WHERE itag_serverid = %s AND itag_id=%s """
                await cur.execute(sql, (server_id, tag_id.upper(),))
                result = await cur.fetchone()
                if result:
                    if os.path.exists(config.itag.path + result['stored_name']):
                        os.remove(config.itag.path + result['stored_name'])
                    sql = """ DELETE FROM discord_itag WHERE `itag_id`=%s AND `itag_serverid`=%s """
                    await cur.execute(sql, (tag_id.upper(), server_id,))
                    await conn.commit()
                    return tag_id.upper()
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_get_allguild():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_server """
                await cur.execute(sql,)
                result = await cur.fetchall()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


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
        await logchanbot(traceback.format_exc())
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
        await logchanbot(traceback.format_exc())


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
        await logchanbot(traceback.format_exc())
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
        await logchanbot(traceback.format_exc())
    return None


async def sql_changeinfo_by_server(server_id: str, what: str, value: str):
    global pool
    if what.lower() in ["servername", "prefix", "default_coin", "tiponly", "numb_user", "numb_bot", "numb_channel", \
    "react_tip", "react_tip_100", "lastUpdate", "botchan", "enable_faucet", "enable_game", "enable_market"]:
        try:
            #print(f"ok try to change {what} to {value}")
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    sql = """ UPDATE discord_server SET `""" + what.lower() + """` = %s WHERE `serverid` = %s """
                    await cur.execute(sql, (value, server_id,))
                    await conn.commit()
        except Exception as e:
            await logchanbot(traceback.format_exc())


async def sql_updatestat_by_server(server_id: str, numb_user: int, numb_bot: int, numb_channel: int, numb_online: int):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE discord_server SET `numb_user` = %s, 
                          `numb_bot`= %s, `numb_channel` = %s, `numb_online` = %s, 
                         `lastUpdate` = %s WHERE `serverid` = %s """
                await cur.execute(sql, (numb_user, numb_bot, numb_channel, numb_online, int(time.time()), server_id,))
                await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_discord_userinfo_get(user_id: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT * FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_userinfo_locked(user_id: str, locked: str, locked_reason: str, locked_by: str):
    global pool
    if locked.upper() not in ["YES", "NO"]:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `user_id` FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                if result is None:
                    sql = """ INSERT INTO `discord_userinfo` (`user_id`, `locked`, `locked_reason`, `locked_by`, `locked_date`)
                          VALUES (%s, %s, %s, %s, %s) """
                    await cur.execute(sql, (user_id, locked.upper(), locked_reason, locked_by, int(time.time())))
                    await conn.commit()
                else:
                    sql = """ UPDATE `discord_userinfo` SET `locked`= %s, `locked_reason` = %s, `locked_by` = %s, `locked_date` = %s
                          WHERE `user_id` = %s """
                    await cur.execute(sql, (locked.upper(), locked_reason, locked_by, int(time.time()), user_id))
                    await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_roach_add(main_id: str, roach_id: str, roach_name: str, main_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `roach_id`, `main_id`, `date` FROM discord_faucetroach 
                          WHERE `roach_id` = %s AND `main_id` = %s """
                await cur.execute(sql, (roach_id, main_id,))
                result = await cur.fetchone()
                if result is None:
                    sql = """ INSERT INTO `discord_faucetroach` (`roach_id`, `main_id`, `roach_name`, `main_name`, `date`)
                          VALUES (%s, %s, %s, %s, %s) """
                    await cur.execute(sql, (roach_id, main_id, roach_name, main_name, int(time.time())))
                    await conn.commit()
                    return True
                else:
                    return None
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_roach_get_by_id(roach_id: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `roach_id`, `main_id`, `date` FROM discord_faucetroach 
                          WHERE (`roach_id` = %s OR `main_id` = %s) AND `user_server`=%s """
                await cur.execute(sql, (roach_id, roach_id, user_server))
                result = await cur.fetchall()
                if result is None:
                    return None
                else:
                    roaches = []
                    for each in result:
                        roaches.append(each['roach_id'])
                        roaches.append(each['main_id'])
                    return set(roaches)
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_userinfo_2fa_insert(user_id: str, twofa_secret: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `user_id` FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                if result is None:
                    sql = """ INSERT INTO `discord_userinfo` (`user_id`, `twofa_secret`, `twofa_activate_ts`)
                          VALUES (%s, %s, %s) """
                    await cur.execute(sql, (user_id, encrypt_string(twofa_secret), int(time.time())))
                    await conn.commit()
                    return True
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_userinfo_2fa_update(user_id: str, twofa_secret: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `user_id` FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                if result:
                    sql = """ UPDATE `discord_userinfo` SET `twofa_secret` = %s, `twofa_activate_ts` = %s 
                          WHERE `user_id`=%s """
                    await cur.execute(sql, (encrypt_string(twofa_secret), int(time.time()), user_id))
                    await conn.commit()
                    return True
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_userinfo_2fa_verify(user_id: str, verify: str):
    if verify.upper() not in ["YES", "NO"]:
        return
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `user_id` FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                if result:
                    sql = """ UPDATE `discord_userinfo` SET `twofa_verified` = %s, `twofa_verified_ts` = %s 
                          WHERE `user_id`=%s """
                    if verify.upper() == "NO":
                        # if unverify, need to clear secret code as well, and disactivate other related 2FA.
                        sql = """ UPDATE `discord_userinfo` SET `twofa_verified` = %s, `twofa_verified_ts` = %s, `twofa_secret` = %s, `twofa_activate_ts` = %s, 
                              `twofa_onoff` = %s, `twofa_active` = %s
                              WHERE `user_id`=%s """
                        await cur.execute(sql, (verify.upper(), int(time.time()), '', int(time.time()), 'OFF', 'NO', user_id))
                        await conn.commit()
                    else:
                        await cur.execute(sql, (verify.upper(), int(time.time()), user_id))
                        await conn.commit()
                    return True
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_change_userinfo_single(user_id: str, what: str, value: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # select first
                sql = """ SELECT `user_id` FROM discord_userinfo 
                          WHERE `user_id` = %s """
                await cur.execute(sql, (user_id,))
                result = await cur.fetchone()
                if result:
                    sql = """ UPDATE discord_userinfo SET `""" + what.lower() + """` = %s WHERE `user_id` = %s """
                    await cur.execute(sql, (value, user_id))
                    await conn.commit()
                else:
                    sql = """ INSERT INTO `discord_userinfo` (`user_id`, `""" + what.lower() + """`)
                          VALUES (%s, %s) """
                    await cur.execute(sql, (user_id, value))
                    await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_addignorechan_by_server(server_id: str, ignorechan: str, by_userid: str, by_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `discord_ignorechan` (`serverid`, `ignorechan`, `set_by_userid`, `by_author`, `set_when`)
                          VALUES (%s, %s, %s, %s, %s) """
                await cur.execute(sql, (server_id, ignorechan, by_userid, by_name, int(time.time())))
                await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_delignorechan_by_server(server_id: str, ignorechan: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ DELETE FROM `discord_ignorechan` WHERE `serverid` = %s AND `ignorechan` = %s """
                await cur.execute(sql, (server_id, ignorechan,))
                await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_listignorechan():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `discord_ignorechan` """
                await cur.execute(sql)
                result = await cur.fetchall()
                ignore_chan = {}
                if result:
                    for row in result:
                        if str(row['serverid']) in ignore_chan:
                            ignore_chan[str(row['serverid'])].append(str(row['ignorechan']))
                        else:
                            ignore_chan[str(row['serverid'])] = []
                            ignore_chan[str(row['serverid'])].append(str(row['ignorechan']))
                    return ignore_chan
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_add_mutechan_by_server(server_id: str, mutechan: str, by_userid: str, by_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `discord_mutechan` (`serverid`, `mutechan`, `set_by_userid`, `by_author`, `set_when`)
                          VALUES (%s, %s, %s, %s, %s) """
                await cur.execute(sql, (server_id, mutechan, by_userid, by_name, int(time.time())))
                await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_del_mutechan_by_server(server_id: str, mutechan: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ DELETE FROM `discord_mutechan` WHERE `serverid` = %s AND `mutechan` = %s """
                await cur.execute(sql, (server_id, mutechan,))
                await conn.commit()
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_list_mutechan():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM `discord_mutechan` """
                await cur.execute(sql)
                result = await cur.fetchall()
                mute_chan = {}
                if result:
                    for row in result:
                        if str(row['serverid']) in mute_chan:
                            mute_chan[str(row['serverid'])].append(str(row['mutechan']))
                        else:
                            mute_chan[str(row['serverid'])] = []
                            mute_chan[str(row['serverid'])].append(str(row['mutechan']))
                    return mute_chan
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_add_logs_tx(list_tx):
    global pool
    if len(list_tx) == 0:
        return 0
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `action_tx_logs` (`uuid`, `action`, `user_id`, `user_name`, 
                          `event_date`, `msg_content`, `user_server`, `end_point`)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.executemany(sql, list_tx)
                await conn.commit()
                return cur.rowcount
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_add_failed_tx(coin: str, user_id: str, user_author: str, amount: int, tx_type: str):
    global pool
    if tx_type.upper() not in ['TIP','TIPS','TIPALL','DONATE','WITHDRAW','SEND', 'REACTTIP', 'FREETIP']:
        return None
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT IGNORE INTO `discord_txfail` (`coin_name`, `user_id`, `tx_author`, `amount`, `tx_type`, `fail_time`)
                          VALUES (%s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (coin.upper(), user_id, user_author, amount, tx_type.upper(), int(time.time())))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_tipnotify():
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT `user_id`, `date` FROM bot_tipnotify_user """
                await cur.execute(sql,)
                result = await cur.fetchall()
                ignorelist = []
                for row in result:
                    ignorelist.append(row['user_id'])
                return ignorelist
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_toggle_tipnotify(user_id: str, onoff: str):
    # Bot will add user_id if it failed to DM
    global pool
    onoff = onoff.upper()
    if onoff == "OFF":
        try:
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    sql = """ SELECT * FROM `bot_tipnotify_user` WHERE `user_id` = %s LIMIT 1 """
                    await cur.execute(sql, (user_id))
                    result = await cur.fetchone()
                    if result is None:
                        sql = """ INSERT INTO `bot_tipnotify_user` (`user_id`, `date`)
                                  VALUES (%s, %s) """    
                        await cur.execute(sql, (user_id, int(time.time())))
                        await conn.commit()
        except pymysql.err.Warning as e:
            await logchanbot(traceback.format_exc())
        except Exception as e:
            await logchanbot(traceback.format_exc())
    elif onoff == "ON":
        try:
            await openConnection()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    sql = """ DELETE FROM `bot_tipnotify_user` WHERE `user_id` = %s """
                    await cur.execute(sql, str(user_id))
                    await conn.commit()
        except Exception as e:
            await logchanbot(traceback.format_exc())


async def sql_updateinfo_by_server(server_id: str, what: str, value: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT serverid, servername, prefix, default_coin, numb_user, numb_bot, tiponly 
                          FROM discord_server WHERE serverid = %s """
                await cur.execute(sql, (server_id,))
                result = await cur.fetchone()
                if result is None:
                    return None
                else:
                    if what in ["servername", "prefix", "default_coin", "tiponly", "status"]:
                        sql = """ UPDATE discord_server SET """+what+"""=%s WHERE serverid=%s """
                        await cur.execute(sql, (what, value, server_id,))
                        await conn.commit()
                    else:
                        return None
    except Exception as e:
        await logchanbot(traceback.format_exc())


# DOGE
async def sql_mv_doge_single(user_from: str, to_user: str, amount: float, coin: str, tiptype: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_COIN_DOGE:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO doge_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `type`, `date`, `user_server`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_from, to_user, amount, tiptype.upper(), int(time.time()), user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_mv_doge_multiple(user_from: str, user_tos, amount_each: float, coin: str, tiptype: str):
    # user_tos is array "account1", "account2", ....
    global pool
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_COIN_DOGE:
        return False
    if tiptype.upper() not in ["TIPS", "TIPALL", "FREETIP", "FREETIPS"]:
        return False
    values_str = []
    currentTs = int(time.time())
    for item in user_tos:
        values_str.append(f"('{COIN_NAME}', '{user_from}', '{item}', {amount_each}, '{tiptype.upper()}', {currentTs})\n")
    values_sql = "VALUES " + ",".join(values_str)
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO doge_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `type`, `date`) 
                          """+values_sql+""" """
                await cur.execute(sql,)
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_external_doge_single(user_from: str, amount: float, fee: float, to_address: str, coin: str, tiptype: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_COIN_DOGE:
        return False
    if tiptype.upper() not in ["SEND", "WITHDRAW"]:
        return False
    try:
        await openConnection()
        print("DOGE EXTERNAL: ")
        print((to_address, amount, user_from, COIN_NAME))
        txHash = await wallet.doge_sendtoaddress(to_address, amount, user_from, COIN_NAME)
        print("COMPLETE DOGE EXTERNAL TX")
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO doge_external_tx (`coin_name`, `user_id`, `amount`, `fee`, `to_address`, 
                          `type`, `date`, `tx_hash`, `user_server`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_from, amount, fee, to_address, tiptype.upper(), int(time.time()), txHash, user_server))
                await conn.commit()
                return txHash
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_doge_balance(userID: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_COIN_DOGE:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT SUM(amount) AS Expense FROM doge_mv_tx WHERE `from_userid`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Expense = result['Expense']
                else:
                    Expense = 0

                sql = """ SELECT SUM(amount) AS Income FROM doge_mv_tx WHERE `to_userid`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Income = result['Income']
                else:
                    Income = 0

                sql = """ SELECT SUM(amount+fee) AS TxExpense FROM doge_external_tx WHERE `user_id`=%s AND `coin_name`=%s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    TxExpense = result['TxExpense']
                else:
                    TxExpense = 0

                sql = """ SELECT SUM(amount) AS SwapIn FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s AND `to` = %s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT', user_server))
                result = await cur.fetchone()
                if result:
                    SwapIn = result['SwapIn']
                else:
                    SwapIn = 0

                sql = """ SELECT SUM(amount) AS SwapOut FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s AND `from` = %s AND `user_server`=%s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT', user_server))
                result = await cur.fetchone()
                if result:
                    SwapOut = result['SwapOut']
                else:
                    SwapOut = 0

                # Credit by admin is positive (Positive)
                sql = """ SELECT SUM(amount) AS Credited FROM credit_balance WHERE `coin_name`=%s AND `to_userid`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Credited = result['Credited']
                else:
                    Credited = 0

                # Voucher create (Negative)
                sql = """ SELECT SUM(amount+reserved_fee) AS Expended_Voucher FROM cn_voucher 
                          WHERE `coin_name`=%s AND `user_id`=%s AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Expended_Voucher = result['Expended_Voucher']
                else:
                    Expended_Voucher = 0

                # Game Credit
                sql = """ SELECT SUM(won_amount) AS GameCredit FROM discord_game WHERE `coin_name`=%s AND `played_user`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    GameCredit = result['GameCredit']
                else:
                    GameCredit = 0

                balance = {}
                balance['Expense'] = Expense or 0
                balance['Expense'] = round(balance['Expense'], 4)
                balance['Income'] = Income or 0
                balance['TxExpense'] = TxExpense or 0
                balance['SwapIn'] = SwapIn or 0
                balance['SwapOut'] = SwapOut or 0
                balance['Credited'] = Credited if Credited else 0
                balance['GameCredit'] = GameCredit if GameCredit else 0
                balance['Expended_Voucher'] = Expended_Voucher if Expended_Voucher else 0
                balance['Adjust'] = float(balance['Credited']) + float(balance['GameCredit']) + float(balance['Income']) + float(balance['SwapIn']) - float(balance['Expense']) \
                - float(balance['TxExpense']) - float(balance['SwapOut']) - float(balance['Expended_Voucher'])
                return balance
    except Exception as e:
        await logchanbot(traceback.format_exc())


# XMR Based
async def sql_mv_xmr_single(user_from: str, to_user: str, amount: float, coin: str, tiptype: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    if coin_family != "XMR":
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO xmroff_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, user_from, to_user, amount, wallet.get_decimal(COIN_NAME), tiptype.upper(), int(time.time()),))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_mv_xmr_multiple(user_from: str, user_tos, amount_each: float, coin: str, tiptype: str):
    # user_tos is array "account1", "account2", ....
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    if coin_family != "XMR":
        return False
    if tiptype.upper() not in ["TIPS", "TIPALL", "FREETIP", "FREETIPS"]:
        return False
    values_str = []
    currentTs = int(time.time())
    for item in user_tos:
        values_str.append(f"('{COIN_NAME}', '{user_from}', '{item}', {amount_each}, {wallet.get_decimal(COIN_NAME)}, '{tiptype.upper()}', {currentTs})\n")
    values_sql = "VALUES " + ",".join(values_str)
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO xmroff_mv_tx (`coin_name`, `from_userid`, `to_userid`, `amount`, `decimal`, `type`, `date`) 
                          """+values_sql+""" """
                await cur.execute(sql,)
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_external_xmr_single(user_from: str, amount: float, to_address: str, coin: str, tiptype: str):
    global pool
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    if coin_family != "XMR":
        return False
    if tiptype.upper() not in ["SEND", "WITHDRAW"]:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if coin_family == "XMR":
                    tx_hash = await wallet.send_transaction('TIPBOT', to_address, 
                                                            amount, COIN_NAME, 0)
                    if tx_hash:
                        updateTime = int(time.time())
                        async with conn.cursor() as cur: 
                            sql = """ INSERT INTO xmroff_external_tx (`coin_name`, `user_id`, `amount`, `fee`, `decimal`, `to_address`, 
                                      `type`, `date`, `tx_hash`, `tx_key`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, user_from, amount, tx_hash['fee'], wallet.get_decimal(COIN_NAME), to_address, tiptype.upper(), int(time.time()), tx_hash['tx_hash'], tx_hash['tx_key'],))
                            await conn.commit()
                            return tx_hash
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_cnoff_balance(userID: str, coin: str, user_server: str = 'DISCORD'):
    global pool, redis_conn, redis_expired
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    if coin_family not in ["TRTL", "BCN"]:
        return False

    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT SUM(amount) AS Expense FROM cnoff_mv_tx WHERE `from_userid`=%s AND `coin_name` = %s AND `user_server` = %s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Expense = result['Expense']
                else:
                    Expense = 0

                sql = """ SELECT SUM(amount) AS Income FROM cnoff_mv_tx WHERE `to_userid`=%s AND `coin_name` = %s AND `user_server` = %s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    Income = result['Income']
                else:
                    Income = 0

                sql = """ SELECT SUM(amount+fee) AS TxExpense FROM cnoff_external_tx WHERE `user_id`=%s AND `coin_name` = %s AND `user_server` = %s """
                await cur.execute(sql, (userID, COIN_NAME, user_server))
                result = await cur.fetchone()
                if result:
                    TxExpense = result['TxExpense']
                else:
                    TxExpense = 0

                sql = """ SELECT SUM(amount) AS SwapIn FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s and `to` = %s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT'))
                result = await cur.fetchone()
                if result:
                    SwapIn = result['SwapIn']
                else:
                    SwapIn = 0

                sql = """ SELECT SUM(amount) AS SwapOut FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s and `from` = %s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT'))
                result = await cur.fetchone()
                if result:
                    SwapOut = result['SwapOut']
                else:
                    SwapOut = 0

                # Credit by admin is positive (Positive)
                sql = """ SELECT SUM(amount) AS Credited FROM credit_balance WHERE `coin_name`=%s AND `to_userid`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Credited = result['Credited']
                else:
                    Credited = 0

                # Voucher create (Negative)
                sql = """ SELECT SUM(amount+reserved_fee) AS Expended_Voucher FROM cn_voucher 
                          WHERE `coin_name`=%s AND `user_id`=%s AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    Expended_Voucher = result['Expended_Voucher']
                else:
                    Expended_Voucher = 0

                # Game Credit
                sql = """ SELECT SUM(won_amount) AS GameCredit FROM discord_game WHERE `coin_name`=%s AND `played_user`=%s 
                      AND `user_server`=%s """
                await cur.execute(sql, (COIN_NAME, userID, user_server))
                result = await cur.fetchone()
                if result:
                    GameCredit = result['GameCredit']
                else:
                    GameCredit = 0

                balance = {}
                balance['Expense'] = float(Expense) if Expense else 0
                balance['Expense'] = float(round(balance['Expense'], 4))
                balance['Income'] = float(Income) if Income else 0
                balance['TxExpense'] = float(TxExpense) if TxExpense else 0
                balance['SwapIn'] = float(SwapIn) if SwapIn else 0
                balance['SwapOut'] = float(SwapOut) if SwapOut else 0
                balance['Credited'] = float(Credited) if Credited else 0
                balance['GameCredit'] = float(GameCredit) if GameCredit else 0
                balance['Expended_Voucher'] = float(Expended_Voucher) if Expended_Voucher else 0
                balance['Adjust'] = balance['Credited'] + balance['GameCredit'] + balance['Income'] + balance['SwapIn'] - balance['Expense'] \
                - balance['TxExpense'] - balance['SwapOut'] - balance['Expended_Voucher']

                return balance
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_xmr_balance(userID: str, coin: str, redis_reset: bool = True):
    global pool, redis_conn, redis_expired
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    if coin_family != "XMR":
        return False
    # Check if exist in redis
    try:
        openRedis()
        if redis_conn and redis_conn.exists(f'TIPBOT:BALANCE_{str(userID)}_{COIN_NAME}'):
            if redis_reset == False:
                return json.loads(redis_conn.get(f'TIPBOT:BALANCE_{str(userID)}_{COIN_NAME}').decode())
            else:
                redis_conn.delete(f'TIPBOT:BALANCE_{str(userID)}_{COIN_NAME}')
    except Exception as e:
        await logchanbot(traceback.format_exc())

    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT SUM(amount) AS Expense FROM xmroff_mv_tx WHERE `from_userid`=%s AND `coin_name` = %s """
                await cur.execute(sql, (userID, COIN_NAME))
                result = await cur.fetchone()
                if result:
                    Expense = result['Expense']
                else:
                    Expense = 0

                sql = """ SELECT SUM(amount) AS Income FROM xmroff_mv_tx WHERE `to_userid`=%s AND `coin_name` = %s """
                await cur.execute(sql, (userID, COIN_NAME))
                result = await cur.fetchone()
                if result:
                    Income = result['Income']
                else:
                    Income = 0

                sql = """ SELECT SUM(amount+fee) AS TxExpense FROM xmroff_external_tx WHERE `user_id`=%s AND `coin_name` = %s """
                await cur.execute(sql, (userID, COIN_NAME))
                result = await cur.fetchone()
                if result:
                    TxExpense = result['TxExpense']
                else:
                    TxExpense = 0

                sql = """ SELECT SUM(amount) AS SwapIn FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s and `to` = %s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT'))
                result = await cur.fetchone()
                if result:
                    SwapIn = result['SwapIn']
                else:
                    SwapIn = 0

                sql = """ SELECT SUM(amount) AS SwapOut FROM discord_swap_balance WHERE `owner_id`=%s AND `coin_name` = %s and `from` = %s """
                await cur.execute(sql, (userID, COIN_NAME, 'TIPBOT'))
                result = await cur.fetchone()
                if result:
                    SwapOut = result['SwapOut']
                else:
                    SwapOut = 0

                # Credit by admin is positive (Positive)
                sql = """ SELECT SUM(amount) AS Credited FROM credit_balance WHERE `coin_name`=%s AND `to_userid`=%s  
                      """
                await cur.execute(sql, (COIN_NAME, userID))
                result = await cur.fetchone()
                if result:
                    Credited = result['Credited']
                else:
                    Credited = 0

                # Voucher create (Negative)
                sql = """ SELECT SUM(amount+reserved_fee) AS Expended_Voucher FROM cn_voucher 
                          WHERE `coin_name`=%s AND `user_id`=%s """
                await cur.execute(sql, (COIN_NAME, userID))
                result = await cur.fetchone()
                if result:
                    Expended_Voucher = result['Expended_Voucher']
                else:
                    Expended_Voucher = 0

                # Game Credit
                sql = """ SELECT SUM(won_amount) AS GameCredit FROM discord_game WHERE `coin_name`=%s AND `played_user`=%s """
                await cur.execute(sql, (COIN_NAME, userID))
                result = await cur.fetchone()
                if result:
                    GameCredit = result['GameCredit']
                else:
                    GameCredit = 0

                balance = {}
                balance['Expense'] = float(Expense) if Expense else 0
                balance['Expense'] = float(round(balance['Expense'], 4))
                balance['Income'] = float(Income) if Income else 0
                balance['TxExpense'] = float(TxExpense) if TxExpense else 0
                balance['Credited'] = float(Credited) if Credited else 0
                balance['GameCredit'] = float(GameCredit) if GameCredit else 0
                balance['SwapIn'] = float(SwapIn) if SwapIn else 0
                balance['SwapOut'] = float(SwapOut) if SwapOut else 0
                balance['Expended_Voucher'] = float(Expended_Voucher) if Expended_Voucher else 0
                balance['Adjust'] = balance['Credited'] + balance['GameCredit'] + balance['Income'] + balance['SwapIn'] - balance['Expense'] - balance['TxExpense'] \
                - balance['SwapOut'] - balance['Expended_Voucher']
                # add to redis
                try:
                    if redis_conn:
                        redis_conn.set(f'TIPBOT:BALANCE_{str(userID)}_{COIN_NAME}', json.dumps(balance), ex=redis_expired)
                except Exception as e:
                    await logchanbot(traceback.format_exc())
                return balance
    except Exception as e:
        await logchanbot(traceback.format_exc())



async def sql_get_userwallet_by_paymentid(paymentid: str, coin: str, user_server: str = 'DISCORD'):
    global pool
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    COIN_NAME = coin.upper()
    coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = None
                if coin_family == "TRTL" or coin_family == "BCN":
                    sql = """ SELECT * FROM cnoff_user_paymentid WHERE `paymentid`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "XMR":
                    sql = """ SELECT * FROM xmroff_user_paymentid WHERE `paymentid`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "DOGE":
                    # if doge family, address is paymentid
                    sql = """ SELECT * FROM doge_user WHERE `balance_wallet_address`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                elif coin_family == "NANO":
                    # if doge family, address is paymentid
                    sql = """ SELECT * FROM nano_user WHERE `balance_wallet_address`=%s AND `coin_name` = %s AND `user_server`=%s LIMIT 1 """
                    await cur.execute(sql, (paymentid, COIN_NAME, user_server))
                    result = await cur.fetchone()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_new_tx_table(notified: str = 'NO', failed_notify: str = 'NO'):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_notify_new_tx WHERE `notified`=%s AND `failed_notify`=%s """
                await cur.execute(sql, (notified, failed_notify,))
                result = await cur.fetchall()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_update_notify_tx_table(payment_id: str, owner_id: str, owner_name: str, notified: str = 'YES', failed_notify: str = 'NO'):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE discord_notify_new_tx SET `owner_id`=%s, `owner_name`=%s, `notified`=%s, `failed_notify`=%s, 
                          `notified_time`=%s WHERE `payment_id`=%s """
                await cur.execute(sql, (owner_id, owner_name, notified, failed_notify, float("%.3f" % time.time()), payment_id,))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_swap_balance(coin: str, owner_id: str, owner_name: str, from_: str, to_: str, amount: float):
    global pool, ENABLE_SWAP
    COIN_NAME = coin.upper()
    if COIN_NAME not in ENABLE_SWAP:
        return False
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO discord_swap_balance (`coin_name`, `owner_id`, `owner_name`, `from`, `to`, `amount`, `decimal`) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (COIN_NAME, owner_id, owner_name, from_, to_, amount, wallet.get_decimal(COIN_NAME)))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_new_swap_table(notified: str = 'NO', failed_notify: str = 'NO'):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_swap_balance WHERE `notified`=%s AND `failed_notify`=%s AND `to` = %s """
                await cur.execute(sql, (notified, failed_notify, 'TIPBOT',))
                result = await cur.fetchall()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_update_notify_swap_table(id: int, notified: str = 'YES', failed_notify: str = 'NO'):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE discord_swap_balance SET `notified`=%s, `failed_notify`=%s, 
                          `notified_time`=%s WHERE `id`=%s """
                await cur.execute(sql, (notified, failed_notify, float("%.3f" % time.time()), id,))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_feedback_add(user_id: str, user_name:str, feedback_id: str, text_in: str, feedback_text: str, howto_contact_back: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `discord_feedback` (`user_id`, `user_name`, `feedback_id`, `text_in`, `feedback_text`, `feedback_date`, `howto_contact_back`)
                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (user_id, user_name, feedback_id, text_in, feedback_text, int(time.time()), howto_contact_back))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_get_feedback_count_last(userID, lastDuration: int):
    global pool
    lapDuration = int(time.time()) - lastDuration
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_feedback WHERE `user_id` = %s AND `feedback_date`>%s 
                          ORDER BY `feedback_date` DESC LIMIT 100 """
                await cur.execute(sql, (userID, lapDuration,))
                result = await cur.fetchall()
                if result is None:
                    return 0
                return len(result) if result else 0
    except Exception as e:
        await logchanbot(traceback.format_exc())


async def sql_feedback_by_ref(ref: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_feedback WHERE `feedback_id`=%s """
                await cur.execute(sql, (ref,))
                result = await cur.fetchone()
                return result if result else None
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_feedback_list_by_user(userid: str, last: int):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_feedback WHERE `user_id`=%s 
                          ORDER BY `feedback_date` DESC LIMIT """+str(last)
                await cur.execute(sql, (userid,))
                result = await cur.fetchall()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


# Remote only
async def sql_depositlink_user(userid: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_depositlink WHERE `user_id`=%s 
                          AND `user_server`=%s """
                await cur.execute(sql, (userid, user_server))
                result = await cur.fetchone()
                return result
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_depositlink_user_create(user_id: str, user_name:str, link_key: str, user_server: str):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `discord_depositlink` (`user_id`, `user_name`, `date_create`, `link_key`, `user_server`)
                          VALUES (%s, %s, %s, %s, %s) """
                await cur.execute(sql, (user_id, user_name, int(time.time()), link_key, user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_depositlink_user_update(user_id: str, what: str, value: str, user_server: str):
    global pool
    user_server = user_server.upper()
    if what.lower() not in ["link_key", "enable"]:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ UPDATE `discord_depositlink` SET `"""+what+"""`=%s, `updated_date`=%s WHERE `user_id`=%s AND `user_server`=%s LIMIT 1 """
                await cur.execute(sql, (value, int(time.time()), user_id, user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_deposit_getall_address_user(userid: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT `coin_name`, `user_id`, `int_address`, `user_server` FROM cnoff_user_paymentid WHERE `user_id`=%s 
                          AND `user_server`=%s """
                await cur.execute(sql, (userid, user_server))
                cnoff_user_paymentid = await cur.fetchall()
                sql = """ SELECT `coin_name`, `user_id`, `int_address`, `user_server` FROM xmroff_user_paymentid WHERE `user_id`=%s 
                          AND `user_server`=%s """
                await cur.execute(sql, (userid, user_server))
                xmroff_user_paymentid = await cur.fetchall()
                sql = """ SELECT `coin_name`, `user_id`, `balance_wallet_address`, `user_server` FROM doge_user WHERE `user_id`=%s 
                          AND `user_server`=%s """
                await cur.execute(sql, (userid, user_server))
                doge_user = await cur.fetchall()
                user_coin_list = {}
                if cnoff_user_paymentid and len(cnoff_user_paymentid) > 0:
                    for each in cnoff_user_paymentid:
                        user_coin_list[each['coin_name']] = each['int_address']
                if xmroff_user_paymentid and len(xmroff_user_paymentid) > 0:
                    for each in xmroff_user_paymentid:
                        user_coin_list[each['coin_name']] = each['int_address']
                if doge_user and len(doge_user) > 0:
                    for each in doge_user:
                        user_coin_list[each['coin_name']] = each['balance_wallet_address']
                return user_coin_list
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_deposit_getall_address_user_remote(userid: str, user_server: str = 'DISCORD'):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_depositlink_address WHERE `user_id`=%s 
                          AND `user_server`=%s """
                await cur.execute(sql, (userid, user_server))
                result = await cur.fetchall()
                user_coin_list = {}
                if result and len(result) > 0:
                    for each in result:
                        user_coin_list[each['coin_name']] = each['deposit_address']
                    return user_coin_list
                else:
                    return None
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_depositlink_user_insert_address(user_id: str, coin_name: str, deposit_address: str, user_server: str):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `discord_depositlink_address` (`user_id`, `coin_name`, `deposit_address`, `user_server`)
                          VALUES (%s, %s, %s, %s) """
                await cur.execute(sql, (user_id, coin_name, deposit_address, user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_depositlink_user_delete_address(user_id: str, coin_name: str, user_server: str):
    global pool
    user_server = user_server.upper()
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ DELETE FROM `discord_depositlink_address` WHERE `user_id`=%s AND `user_server`=%s and `coin_name`=%s """
                await cur.execute(sql, (user_id, user_server, coin_name))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_miningpoolstat_fetch(coin_name: str, user_id: str, user_name: str, requested_date: int, \
respond_date: int, response: str, guild_id: str, guild_name: str, channel_id: str, is_cache: str='NO', user_server: str='DISCORD', using_browser: str='NO'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `miningpoolstat_fetch` (`coin_name`, `user_id`, `user_name`, `requested_date`, `respond_date`, 
                          `response`, `guild_id`, `guild_name`, `channel_id`, `user_server`, `is_cache`, `using_browser`)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (coin_name, user_id, user_name, requested_date, respond_date, response, guild_id, 
                                        guild_name, channel_id, user_server, is_cache, using_browser))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_add_tbfun(user_id: str, user_name: str, channel_id: str, guild_id: str, \
guild_name: str, funcmd: str, msg_content: str, user_server: str='DISCORD'):
    global pool
    user_server = user_server.upper()
    if user_server not in ['DISCORD', 'TELEGRAM']:
        return
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ INSERT INTO `discord_tbfun` (`user_id`, `user_name`, `channel_id`, `guild_id`, `guild_name`, 
                          `funcmd`, `msg_content`, `time`, `user_server`)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                await cur.execute(sql, (user_id, user_name, channel_id, guild_id, guild_name, funcmd, msg_content, 
                                        int(time.time()), user_server))
                await conn.commit()
                return True
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return False


async def sql_game_get_level_tpl(level: int, game_name: str):
    global pool
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_game_level_tpl WHERE `level`=%s 
                          AND `game_name`=%s LIMIT 1 """
                await cur.execute(sql, (level, game_name.upper()))
                result = await cur.fetchone()
                if result and len(result) > 0:
                    return result
                else:
                    return None
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


async def sql_game_get_level_user(userid: str, game_name: str):
    global pool
    level = -1
    try:
        await openConnection()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """ SELECT * FROM discord_game WHERE `played_user`=%s 
                          AND `game_type`=%s AND `win_lose`=%s ORDER BY `played_at` DESC LIMIT 1 """
                await cur.execute(sql, (userid, game_name.upper(), 'WIN'))
                result = await cur.fetchone()
                if result and len(result) > 0:
                    try:
                        level = int(result['game_result'])
                    except Exception as e:
                        await logchanbot(traceback.format_exc())

                sql = """ SELECT * FROM discord_game_free WHERE `played_user`=%s 
                          AND `game_type`=%s AND `win_lose`=%s ORDER BY `played_at` DESC LIMIT 1 """
                await cur.execute(sql, (userid, game_name.upper(), 'WIN'))
                result = await cur.fetchone()
                if result and len(result) > 0:
                    try:
                        if level and int(result['game_result']) > level:
                            level = int(result['game_result'])
                    except Exception as e:
                        await logchanbot(traceback.format_exc())
                return level
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return level


# original ValueInUSD
async def market_value_in_usd(amount, ticker) -> str:
    global pool_cmc
    try:
        await openConnection_cmc()
        async with pool_cmc.acquire() as conn:
            async with conn.cursor() as cur:
                # Read a single record from cmc_v2
                sql = """ SELECT * FROM `cmc_v2` WHERE `symbol`=%s ORDER BY `id` DESC LIMIT 1 """
                await cur.execute(sql, (ticker.upper()))
                result = await cur.fetchone()

                sql = """ SELECT * FROM `coingecko_v2` WHERE `symbol`=%s ORDER BY `id` DESC LIMIT 1 """
                await cur.execute(sql, (ticker.lower()))
                result2 = await cur.fetchone()

            if all(v is None for v in [result, result2]):
                # return 'We can not find ticker {} in Coinmarketcap or CoinGecko'.format(ticker.upper())
                return None
            else:
                market_price = {}
                if result:
                    name = result['name']
                    ticker = result['symbol'].upper()
                    price = result['priceUSD']
                    totalValue = amount * price
                    # update = datetime.datetime.strptime(result['last_updated'].split(".")[0], '%Y-%m-%dT%H:%M:%S')
                    market_price['cmc_price'] = price
                    market_price['cmc_totalvalue'] = totalValue
                    market_price['cmc_update'] = result['last_updated']
                if result2:				
                    name2 = result2['name']
                    ticker2 = result2['symbol'].upper()
                    price2 = result2['marketprice_USD']
                    totalValue2 = amount * price2
                    market_price['cg_price'] = price2
                    market_price['cg_totalvalue'] = totalValue2
                    market_price['cg_update'] = result2['last_updated']
                return market_price
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


# original ValueCmcUSD
async def market_value_cmc_usd(ticker) -> float:
    global pool_cmc
    try:
        await openConnection_cmc()
        async with pool_cmc.acquire() as conn:
            async with conn.cursor() as cur:
                # Read a single record from cmc_v2
                sql = """ SELECT * FROM `cmc_v2` WHERE `symbol`=%s ORDER BY `id` DESC LIMIT 1 """
                await cur.execute(sql, (ticker.upper()))
                result = await cur.fetchone()
                if result: return float(result['priceUSD'])
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


# original ValueGeckoUSD
async def market_value_cg_usd(ticker) -> float:
    global pool_cmc
    try:
        await openConnection_cmc()
        async with pool_cmc.acquire() as conn:
            async with conn.cursor() as cur:
                # Read a single record from cmc_v2
                sql = """ SELECT * FROM `coingecko_v2` WHERE `symbol`=%s ORDER BY `id` DESC LIMIT 1 """
                await cur.execute(sql, (ticker.lower()))
                result = await cur.fetchone()
                if result: return float(result['marketprice_USD'])
    except Exception as e:
        await logchanbot(traceback.format_exc())
    return None


# Steal from https://nitratine.net/blog/post/encryption-and-decryption-in-python/
def encrypt_string(to_encrypt: str):
    key = (config.encrypt.key).encode()

    # Encrypt
    message = to_encrypt.encode()
    f = Fernet(key)
    encrypted = f.encrypt(message)
    return encrypted.decode()


def decrypt_string(decrypted: str):
    key = (config.encrypt.key).encode()

    # Decrypt
    f = Fernet(key)
    decrypted = f.decrypt(decrypted.encode())
    return decrypted.decode()
