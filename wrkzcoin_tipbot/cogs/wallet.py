import sys, os
import traceback
from datetime import datetime
from decimal import Decimal
import disnake
from disnake.ext import commands, tasks
from disnake.enums import OptionType
from disnake.app_commands import Option
import time
import functools
import aiohttp, asyncio
import json

import numpy as np

import qrcode
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict
import uuid

from web3 import Web3
from web3.middleware import geth_poa_middleware
from ethtoken.abi import EIP20_ABI

from tronpy import AsyncTron
from tronpy.async_contract import AsyncContract, ShieldedTRC20, AsyncContractMethod
from tronpy.providers.async_http import AsyncHTTPProvider
from tronpy.exceptions import AddressNotFound
from tronpy.keys import PrivateKey

from httpx import AsyncClient, Timeout, Limits

from eth_account import Account

from pywallet import wallet as ethwallet
import ssl

import store, utils
import cn_addressvalidation

from Bot import get_token_list, num_format_coin, logchanbot, EMOJI_ZIPPED_MOUTH, EMOJI_ERROR, EMOJI_RED_NO, EMOJI_ARROW_RIGHTHOOK, SERVER_BOT, RowButton_close_message, RowButton_row_close_any_message, human_format, text_to_num, truncate, seconds_str, encrypt_string, decrypt_string
from config import config
import redis_utils

Account.enable_unaudited_hdwallet_features()


class Wallet(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        redis_utils.openRedis()
        self.notify_new_tx_user_noconfirmation.start()
        self.notify_new_tx_user.start()

        # nano, banano
        self.update_balance_nano.start()
        # TRTL-API
        self.update_balance_trtl_api.start()
        # TRTL-SERVICE
        self.update_balance_trtl_service.start()
        # XMR
        self.update_balance_xmr.start()
        # BTC
        self.update_balance_btc.start()
        # CHIA
        self.update_balance_chia.start()


    # Notify user
    @tasks.loop(seconds=15.0)
    async def notify_new_tx_user(self):
        await asyncio.sleep(5.0)
        pending_tx = await store.sql_get_new_tx_table('NO', 'NO')
        if len(pending_tx) > 0:
            # let's notify_new_tx_user
            for eachTx in pending_tx:
                try:
                    COIN_NAME = eachTx['coin_name']
                    coin_family = getattr(getattr(self.bot.coin_list, COIN_NAME), "type")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    if coin_family in ["TRTL-API", "TRTL-SERVICE", "BCN", "XMR", "BTC", "CHIA", "NANO"]:
                        user_tx = await store.sql_get_userwallet_by_paymentid(eachTx['payment_id'], eachTx['coin_name'], coin_family, SERVER_BOT)
                        # if eachTx['coin_name'] == "PGO": print(user_tx)
                        if user_tx:
                            user_found = self.bot.get_user(int(user_tx['user_id']))
                            if user_found:
                                is_notify_failed = False
                                try:
                                    msg = None
                                    if coin_family == "NANO":
                                        msg = "You got a new deposit: ```" + "Coin: {}\nAmount: {}".format(eachTx['coin_name'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False)) + "```"   
                                    elif coin_family != "BTC":
                                        msg = "You got a new deposit confirmed: ```" + "Coin: {}\nTx: {}\nAmount: {}\nHeight: {:,.0f}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['height']) + "```"                         
                                    else:
                                        msg = "You got a new deposit confirmed: ```" + "Coin: {}\nTx: {}\nAmount: {}\nBlock Hash: {}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['blockhash']) + "```"
                                    await user_found.send(msg)
                                except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                                    is_notify_failed = True
                                    pass
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                    await logchanbot(traceback.format_exc())
                                update_notify_tx = await store.sql_update_notify_tx_table(eachTx['payment_id'], user_tx['user_id'], user_found.name, 'YES', 'NO' if is_notify_failed == False else 'YES')
                            else:
                                # try to find if it is guild
                                guild_found = self.bot.get_guild(int(user_tx['user_id']))
                                if guild_found: user_found = self.bot.get_user(guild_found.owner.id)
                                if guild_found and user_found:
                                    is_notify_failed = False
                                    try:
                                        msg = None
                                        if coin_family == "NANO":
                                            msg = "Your guild `{}` got a new deposit: ```" + "Coin: {}\nAmount: {}".format(guild_found.name, eachTx['coin_name'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False)) + "```"   
                                        elif coin_family != "BTC":
                                            msg = "Your guild `{}` got a new deposit confirmed: ```" + "Coin: {}\nTx: {}\nAmount: {}\nHeight: {:,.0f}".format(guild_found.name, eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['height']) + "```"                         
                                        else:
                                            msg = "Your guild `{}` got a new deposit confirmed: ```" + "Coin: {}\nTx: {}\nAmount: {}\nBlock Hash: {}".format(guild_found.name, eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['blockhash']) + "```"
                                        await user_found.send(msg)
                                    except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                                        is_notify_failed = True
                                        pass
                                    except Exception as e:
                                        traceback.print_exc(file=sys.stdout)
                                        await logchanbot(traceback.format_exc())
                                    update_notify_tx = await store.sql_update_notify_tx_table(eachTx['payment_id'], user_tx['user_id'], guild_found.name, 'YES', 'NO' if is_notify_failed == False else 'YES')
                                else:
                                    #print('Can not find user id {} to notification tx: {}'.format(user_tx['user_id'], eachTx['txid']))
                                    pass
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())


    @tasks.loop(seconds=10.0)
    async def notify_new_tx_user_noconfirmation(self):
        await asyncio.sleep(5.0)
        if config.notify_new_tx.enable_new_no_confirm == 1:
            key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
            key_tx_no_confirmed_sent = config.redis.prefix_new_tx + 'NOCONFIRM:SENT'
            try:
                if redis_utils.redis_conn.llen(key_tx_new) > 0:
                    list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                    list_new_tx_sent = redis_utils.redis_conn.lrange(key_tx_no_confirmed_sent, 0, -1) # byte list with b'xxx'
                    # Unique the list
                    list_new_tx = np.unique(list_new_tx).tolist()
                    list_new_tx_sent = np.unique(list_new_tx_sent).tolist()
                    for tx in list_new_tx:
                        try:
                            if tx not in list_new_tx_sent:
                                tx = tx.decode() # decode byte from b'xxx to xxx
                                key_tx_json = config.redis.prefix_new_tx + tx
                                eachTx = None
                                try:
                                    if redis_utils.redis_conn.exists(key_tx_json): eachTx = json.loads(redis_utils.redis_conn.get(key_tx_json).decode())
                                except Exception as e:
                                    traceback.print_exc(file=sys.stdout)
                                if eachTx is None: continue

                                COIN_NAME = eachTx['coin_name']
                                coin_family = getattr(getattr(self.bot.coin_list, COIN_NAME), "type")
                                if eachTx and coin_family in ["TRTL-API", "TRTL-SERVICE", "BCN", "XMR", "BTC", "CHIA"]:
                                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                                    user_tx = await store.sql_get_userwallet_by_paymentid(eachTx['payment_id'], eachTx['coin_name'], coin_family, SERVER_BOT)
                                    if user_tx:
                                        user_found = self.bot.get_user(int(user_tx['user_id']))
                                        if user_found:
                                            try:
                                                msg = None
                                                confirmation_number_txt = "{} needs {} confirmations.".format(eachTx['coin_name'], get_confirm_depth)
                                                if coin_family != "BTC":
                                                    msg = "You got a new **pending** deposit: ```" + "Coin: {}\nTx: {}\nAmount: {}\nHeight: {:,.0f}\n{}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['height'], confirmation_number_txt) + "```"
                                                else:
                                                    msg = "You got a new **pending** deposit: ```" + "Coin: {}\nTx: {}\nAmount: {}\nBlock Hash: {}\n{}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['blockhash'], confirmation_number_txt) + "```"
                                                await user_found.send(msg)
                                            except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                                                pass
                                            # TODO:
                                            redis_utils.redis_conn.lpush(key_tx_no_confirmed_sent, tx)
                                        else:
                                            # try to find if it is guild
                                            guild_found = self.bot.get_guild(int(user_tx['user_id']))
                                            if guild_found: user_found =self.bot.get_user(guild_found.owner.id)
                                            if guild_found and user_found:
                                                try:
                                                    msg = None
                                                    confirmation_number_txt = "{} needs {} confirmations.".format(eachTx['coin_name'], get_confirm_depth)
                                                    if eachTx['coin_name'] != "BTC":
                                                        msg = "Your guild got a new **pending** deposit: ```" + "Coin: {}\nTx: {}\nAmount: {}\nHeight: {:,.0f}\n{}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['height'], confirmation_number_txt) + "```"
                                                    else:
                                                        msg = "Your guild got a new **pending** deposit: ```" + "Coin: {}\nTx: {}\nAmount: {}\nBlock Hash: {}\n{}".format(eachTx['coin_name'], eachTx['txid'], num_format_coin(eachTx['amount'], eachTx['coin_name'], coin_decimal, False), eachTx['blockhash'], confirmation_number_txt) + "```"
                                                    await user_found.send(msg)
                                                except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                                                    pass
                                                except Exception as e:
                                                    traceback.print_exc(file=sys.stdout)
                                                redis_utils.redis_conn.lpush(key_tx_no_confirmed_sent, tx)
                                            else:
                                                # print('Can not find user id {} to notification **pending** tx: {}'.format(user_tx['user_id'], eachTx['txid']))
                                                pass
                                else:
                                    redis_utils.redis_conn.lpush(key_tx_no_confirmed_sent, tx)
                        except Exception as e:
                            traceback.print_exc(file=sys.stdout)
                            await logchanbot(traceback.format_exc())
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
                await logchanbot(traceback.format_exc())


    @tasks.loop(seconds=20.0)
    async def update_balance_trtl_api(self):
        await asyncio.sleep(5.0)
        try:
            # async def trtl_api_get_transfers(self, url: str, key: str, coin: str, height_start: int = None, height_end: int = None):
            list_trtl_api = await store.get_coin_settings("TRTL-API")
            if len(list_trtl_api) > 0:
                list_coins = [each['coin_name'].upper() for each in list_trtl_api]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    gettopblock = await self.gettopblock(COIN_NAME, time_out=32)
                    height = int(gettopblock['block_header']['height'])
                    try:
                        redis_utils.redis_conn.set(f'{config.redis.prefix+config.redis.daemon_height}{COIN_NAME}', str(height))
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                        await logchanbot(traceback.format_exc())

                    url = getattr(getattr(self.bot.coin_list, COIN_NAME), "wallet_address")
                    key = getattr(getattr(self.bot.coin_list, COIN_NAME), "header")
                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    get_min_deposit_amount = int(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit") * 10**coin_decimal)
                    
                    get_transfers = await self.trtl_api_get_transfers(url, key, COIN_NAME, height - 2000, height)
                    list_balance_user = {}
                    if get_transfers and len(get_transfers) >= 1:
                        await store.openConnection()
                        async with store.pool.acquire() as conn:
                            async with conn.cursor() as cur:
                                sql = """ SELECT * FROM `cn_get_transfers` WHERE `coin_name` = %s """
                                await cur.execute(sql, (COIN_NAME,))
                                result = await cur.fetchall()
                                d = [i['txid'] for i in result]
                                # print('=================='+COIN_NAME+'===========')
                                # print(d)
                                # print('=================='+COIN_NAME+'===========')
                                for tx in get_transfers:
                                    # Could be one block has two or more tx with different payment ID
                                    # add to balance only confirmation depth meet
                                    if len(tx['transfers']) > 0 and height >= int(tx['blockHeight']) + get_confirm_depth and tx['transfers'][0]['amount'] >= get_min_deposit_amount and 'paymentID' in tx:
                                        if 'paymentID' in tx and tx['paymentID'] in list_balance_user:
                                            if tx['transfers'][0]['amount'] > 0:
                                                list_balance_user[tx['paymentID']] += tx['transfers'][0]['amount']
                                        elif 'paymentID' in tx and tx['paymentID'] not in list_balance_user:
                                            if tx['transfers'][0]['amount'] > 0:
                                                list_balance_user[tx['paymentID']] = tx['transfers'][0]['amount']
                                        try:
                                            if tx['hash'] not in d:
                                                addresses = tx['transfers']
                                                address = ''
                                                for each_add in addresses:
                                                    if len(each_add['address']) > 0: address = each_add['address']
                                                    break

                                                sql = """ INSERT IGNORE INTO `cn_get_transfers` (`coin_name`, `txid`, `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                await cur.execute(sql, (COIN_NAME, tx['hash'], tx['paymentID'], tx['blockHeight'], tx['timestamp'], float(int(tx['transfers'][0]['amount'])/10**coin_decimal), float(int(tx['fee'])/10**coin_decimal), coin_decimal, address, int(time.time())))
                                                await conn.commit()
                                                # add to notification list also
                                                sql = """ INSERT IGNORE INTO `discord_notify_new_tx` (`coin_name`, `txid`, `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                                          VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                                await cur.execute(sql, (COIN_NAME, tx['hash'], tx['paymentID'], tx['blockHeight'], float(int(tx['transfers'][0]['amount'])/10**coin_decimal), float(int(tx['fee'])/10**coin_decimal), coin_decimal))
                                                await conn.commit()
                                        except Exception as e:
                                            traceback.print_exc(file=sys.stdout)
                                            traceback.print_exc(file=sys.stdout)
                                            await logchanbot(traceback.format_exc())
                                    elif len(tx['transfers']) > 0 and height < int(tx['blockHeight']) + get_confirm_depth and tx['transfers'][0]['amount'] >= get_min_deposit_amount and 'paymentID' in tx:
                                        # add notify to redis and alert deposit. Can be clean later?
                                        if config.notify_new_tx.enable_new_no_confirm == 1:
                                            key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
                                            key_tx_json = config.redis.prefix_new_tx + tx['hash']
                                            try:
                                                if redis_utils.redis_conn.llen(key_tx_new) > 0:
                                                    list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                                                    if list_new_tx and len(list_new_tx) > 0 and tx['hash'].encode() not in list_new_tx:
                                                        redis_utils.redis_conn.lpush(key_tx_new, tx['hash'])
                                                        redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['hash'], 'payment_id': tx['paymentID'], 'height': tx['blockHeight'], 'amount': float(int(tx['transfers'][0]['amount'])/10**coin_decimal), 'fee': float(int(tx['fee'])/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                                elif redis_utils.redis_conn.llen(key_tx_new) == 0:
                                                    redis_utils.redis_conn.lpush(key_tx_new, tx['hash'])
                                                    redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['hash'], 'payment_id': tx['paymentID'], 'height': tx['blockHeight'], 'amount': float(int(tx['transfers'][0]['amount'])/10**coin_decimal), 'fee': float(int(tx['fee'])/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                            except Exception as e:
                                                traceback.print_exc(file=sys.stdout)
                                                await logchanbot(traceback.format_exc())
                                # TODO: update balance cache
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())



    @tasks.loop(seconds=20.0)
    async def update_balance_trtl_service(self):
        await asyncio.sleep(5.0)
        try:
            list_trtl_service = await store.get_coin_settings("TRTL-SERVICE")
            if len(list_trtl_service) > 0:
                list_coins = [each['coin_name'].upper() for each in list_trtl_service]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    gettopblock = await self.gettopblock(COIN_NAME, time_out=32)
                    height = int(gettopblock['block_header']['height'])
                    try:
                        redis_utils.redis_conn.set(f'{config.redis.prefix+config.redis.daemon_height}{COIN_NAME}', str(height))
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                        await logchanbot(traceback.format_exc())

                    url = getattr(getattr(self.bot.coin_list, COIN_NAME), "wallet_address")
                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    get_min_deposit_amount = int(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit") * 10**coin_decimal)
                    
                    get_transfers = await self.trtl_service_getTransactions(url, COIN_NAME, height - 2000, height)
                    list_balance_user = {}
                    if get_transfers and len(get_transfers) >= 1:
                        await store.openConnection()
                        async with store.pool.acquire() as conn:
                            async with conn.cursor() as cur:
                                sql = """ SELECT * FROM `cn_get_transfers` WHERE `coin_name` = %s """
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
                                        if height >= int(tx['blockIndex']) + get_confirm_depth and tx['amount'] >= get_min_deposit_amount and 'paymentId' in tx:
                                            if 'paymentId' in tx and tx['paymentId'] in list_balance_user:
                                                if tx['amount'] > 0: list_balance_user[tx['paymentId']] += tx['amount']
                                            elif 'paymentId' in tx and tx['paymentId'] not in list_balance_user:
                                                if tx['amount'] > 0: list_balance_user[tx['paymentId']] = tx['amount']
                                            try:
                                                if tx['transactionHash'] not in d:
                                                    addresses = tx['transfers']
                                                    address = ''
                                                    for each_add in addresses:
                                                        if len(each_add['address']) > 0: address = each_add['address']
                                                        break
                                                        
                                                    sql = """ INSERT IGNORE INTO `cn_get_transfers` (`coin_name`, `txid`, `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                    await cur.execute(sql, (COIN_NAME, tx['transactionHash'], tx['paymentId'], tx['blockIndex'], tx['timestamp'], float(tx['amount']/10**coin_decimal), float(tx['fee']/10**coin_decimal), coin_decimal, address, int(time.time())))
                                                    await conn.commit()
                                                    # add to notification list also
                                                    sql = """ INSERT IGNORE INTO `discord_notify_new_tx` (`coin_name`, `txid`, `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                                              VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                                    await cur.execute(sql, (COIN_NAME, tx['transactionHash'], tx['paymentId'], tx['blockIndex'], float(tx['amount']/10**coin_decimal), float(tx['fee']/10**coin_decimal), coin_decimal))
                                                    await conn.commit()
                                            except Exception as e:
                                                traceback.print_exc(file=sys.stdout)
                                                await logchanbot(traceback.format_exc())
                                        elif height < int(tx['blockIndex']) + get_confirm_depth and tx['amount'] >= get_min_deposit_amount and 'paymentId' in tx:
                                            # add notify to redis and alert deposit. Can be clean later?
                                            if config.notify_new_tx.enable_new_no_confirm == 1:
                                                key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
                                                key_tx_json = config.redis.prefix_new_tx + tx['transactionHash']
                                                try:
                                                    if redis_utils.redis_conn.llen(key_tx_new) > 0:
                                                        list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                                                        if list_new_tx and len(list_new_tx) > 0 and tx['transactionHash'].encode() not in list_new_tx:
                                                            redis_utils.redis_conn.lpush(key_tx_new, tx['transactionHash'])
                                                            redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['transactionHash'], 'payment_id': tx['paymentId'], 'height': tx['blockIndex'], 'amount': float(tx['amount']/10**coin_decimal), 'fee': tx['fee'], 'decimal': coin_decimal}), ex=86400)
                                                    elif redis_utils.redis_conn.llen(key_tx_new) == 0:
                                                        redis_utils.redis_conn.lpush(key_tx_new, tx['transactionHash'])
                                                        redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['transactionHash'], 'payment_id': tx['paymentId'], 'height': tx['blockIndex'], 'amount': float(tx['amount']/10**coin_decimal), 'fee': tx['fee'], 'decimal': coin_decimal}), ex=86400)
                                                except Exception as e:
                                                    traceback.print_exc(file=sys.stdout)
                                                    await logchanbot(traceback.format_exc())
                    # TODO: update user balance
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())


    @tasks.loop(seconds=20.0)
    async def update_balance_xmr(self):
        await asyncio.sleep(5.0)
        try:
            list_xmr_api = await store.get_coin_settings("XMR")
            if len(list_xmr_api) > 0:
                list_coins = [each['coin_name'].upper() for each in list_xmr_api]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    gettopblock = await self.gettopblock(COIN_NAME, time_out=32)
                    height = int(gettopblock['block_header']['height'])
                    try:
                        redis_utils.redis_conn.set(f'{config.redis.prefix+config.redis.daemon_height}{COIN_NAME}', str(height))
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                        await logchanbot(traceback.format_exc())

                    url = getattr(getattr(self.bot.coin_list, COIN_NAME), "wallet_address")
                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    get_min_deposit_amount = int(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit") * 10**coin_decimal)

                    payload = {
                        "in" : True,
                        "out": True,
                        "pending": False,
                        "failed": False,
                        "pool": False,
                        "filter_by_height": True,
                        "min_height": height - 2000,
                        "max_height": height
                    }
                    get_transfers = await self.call_aiohttp_wallet_xmr_bcn('get_transfers', COIN_NAME, payload=payload)
                    if get_transfers and len(get_transfers) >= 1 and 'in' in get_transfers:
                        try:
                            await store.openConnection()
                            async with store.pool.acquire() as conn:
                                async with conn.cursor() as cur:
                                    sql = """ SELECT * FROM `cn_get_transfers` WHERE `coin_name` = %s """
                                    await cur.execute(sql, (COIN_NAME,))
                                    result = await cur.fetchall()
                                    d = [i['txid'] for i in result]
                                    # print('=================='+COIN_NAME+'===========')
                                    # print(d)
                                    # print('=================='+COIN_NAME+'===========')
                                    list_balance_user = {}
                                    for tx in get_transfers['in']:
                                        # add to balance only confirmation depth meet
                                        if height >= int(tx['height']) + get_confirm_depth and tx['amount'] >= get_min_deposit_amount and 'payment_id' in tx:
                                            if 'payment_id' in tx and tx['payment_id'] in list_balance_user:
                                                list_balance_user[tx['payment_id']] += tx['amount']
                                            elif 'payment_id' in tx and tx['payment_id'] not in list_balance_user:
                                                list_balance_user[tx['payment_id']] = tx['amount']
                                            try:
                                                if tx['txid'] not in d:
                                                    tx_address = tx['address'] if COIN_NAME != "LTHN" else getattr(getattr(self.bot.coin_list, COIN_NAME), "MainAddress")
                                                    sql = """ INSERT IGNORE INTO `cn_get_transfers` (`coin_name`, `in_out`, `txid`, `payment_id`, `height`, `timestamp`, `amount`, `fee`, `decimal`, `address`, time_insert) 
                                                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                    await cur.execute(sql, (COIN_NAME, tx['type'].upper(), tx['txid'], tx['payment_id'], tx['height'], tx['timestamp'], float(tx['amount']/10**coin_decimal), float(tx['fee']/10**coin_decimal), coin_decimal, tx_address, int(time.time())))
                                                    await conn.commit()
                                                    # add to notification list also
                                                    sql = """ INSERT IGNORE INTO `discord_notify_new_tx` (`coin_name`, `txid`, `payment_id`, `height`, `amount`, `fee`, `decimal`) 
                                                              VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                                    await cur.execute(sql, (COIN_NAME, tx['txid'], tx['payment_id'], tx['height'], float(tx['amount']/10**coin_decimal), float(tx['fee']/10**coin_decimal), coin_decimal))
                                                    await conn.commit()
                                            except Exception as e:
                                                await logchanbot(traceback.format_exc())
                                        elif height < int(tx['height']) + get_confirm_depth and tx['amount'] >= get_min_deposit_amount and 'payment_id' in tx:
                                            # add notify to redis and alert deposit. Can be clean later?
                                            if config.notify_new_tx.enable_new_no_confirm == 1:
                                                key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
                                                key_tx_json = config.redis.prefix_new_tx + tx['txid']
                                                try:
                                                    if redis_utils.redis_conn.llen(key_tx_new) > 0:
                                                        list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                                                        if list_new_tx and len(list_new_tx) > 0 and tx['txid'].encode() not in list_new_tx:
                                                            redis_utils.redis_conn.lpush(key_tx_new, tx['txid'])
                                                            redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['payment_id'], 'height': tx['height'], 'amount': float(tx['amount']/10**coin_decimal), 'fee': float(tx['fee']/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                                    elif redis_utils.redis_conn.llen(key_tx_new) == 0:
                                                        redis_utils.redis_conn.lpush(key_tx_new, tx['txid'])
                                                        redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['payment_id'], 'height': tx['height'], 'amount': float(tx['amount']/10**coin_decimal), 'fee': float(tx['fee']/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                                except Exception as e:
                                                    traceback.print_exc(file=sys.stdout)
                                                    await logchanbot(traceback.format_exc())
                                    # TODO: update user balance
                        except Exception as e:
                            traceback.print_exc(file=sys.stdout)
                            await logchanbot(traceback.format_exc())
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())

    @tasks.loop(seconds=20.0)
    async def update_balance_btc(self):
        await asyncio.sleep(5.0)
        try:
            # async def trtl_api_get_transfers(self, url: str, key: str, coin: str, height_start: int = None, height_end: int = None):
            list_btc_api = await store.get_coin_settings("BTC")
            if len(list_btc_api) > 0:
                list_coins = [each['coin_name'].upper() for each in list_btc_api]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    gettopblock = await self.call_doge('getblockchaininfo', COIN_NAME)
                    height = int(gettopblock['blocks'])
                    try:
                        redis_utils.redis_conn.set(f'{config.redis.prefix+config.redis.daemon_height}{COIN_NAME}', str(height))
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                        await logchanbot(traceback.format_exc())

                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    get_min_deposit_amount = int(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit") * 10**coin_decimal)

                    payload = '"*", 50, 0'
                    get_transfers = await self.call_doge('listtransactions', COIN_NAME, payload=payload)
                    if get_transfers and len(get_transfers) >= 1:
                        try:
                            await store.openConnection()
                            async with store.pool.acquire() as conn:
                                async with conn.cursor() as cur:
                                    sql = """ SELECT * FROM `doge_get_transfers` WHERE `coin_name` = %s AND `category` IN (%s, %s) """
                                    await cur.execute(sql, (COIN_NAME, 'receive', 'send'))
                                    result = await cur.fetchall()
                                    d = [i['txid'] for i in result]
                                    # print('=================='+COIN_NAME+'===========')
                                    # print(d)
                                    # print('=================='+COIN_NAME+'===========')
                                    list_balance_user = {}
                                    for tx in get_transfers:
                                        # if COIN_NAME == "PGO": print(tx)
                                        # add to balance only confirmation depth meet
                                        if get_confirm_depth <= int(tx['confirmations']) and tx['amount'] >= get_min_deposit_amount:
                                            if 'address' in tx and tx['address'] in list_balance_user and tx['amount'] > 0:
                                                list_balance_user[tx['address']] += tx['amount']
                                            elif 'address' in tx and tx['address'] not in list_balance_user and tx['amount'] > 0:
                                                list_balance_user[tx['address']] = tx['amount']
                                            try:
                                                if tx['txid'] not in d:
                                                    # print(tx['txid'] + " not in DB.")
                                                    # generate from mining
                                                    if tx['category'] == "receive" or tx['category'] == "generate":
                                                        sql = """ INSERT IGNORE INTO `doge_get_transfers` (`coin_name`, `txid`, `blockhash`, `address`, `blocktime`, `amount`, `fee`, `confirmations`, `category`, `time_insert`) 
                                                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                        await cur.execute(sql, (COIN_NAME, tx['txid'], tx['blockhash'], tx['address'], tx['blocktime'], float(tx['amount']), float(tx['fee']) if 'fee' in tx else None, tx['confirmations'], tx['category'], int(time.time())))
                                                        await conn.commit()
                                                    # add to notification list also, doge payment_id = address
                                                    if (tx['amount'] > 0) and tx['category'] == 'receive':
                                                        sql = """ INSERT IGNORE INTO `discord_notify_new_tx` (`coin_name`, `txid`, `payment_id`, `blockhash`, `amount`, `fee`, `decimal`) 
                                                                  VALUES (%s, %s, %s, %s, %s, %s, %s) """
                                                        await cur.execute(sql, (COIN_NAME, tx['txid'], tx['address'], tx['blockhash'], float(tx['amount']), float(tx['fee']) if 'fee' in tx else None, coin_decimal))
                                                        await conn.commit()
                                            except Exception as e:
                                                traceback.print_exc(file=sys.stdout)
                                                await logchanbot(traceback.format_exc())
                                        if get_confirm_depth > int(tx['confirmations']) > 0 and tx['amount'] >= get_min_deposit_amount:
                                            # add notify to redis and alert deposit. Can be clean later?
                                            if config.notify_new_tx.enable_new_no_confirm == 1:
                                                key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
                                                key_tx_json = config.redis.prefix_new_tx + tx['txid']
                                                try:
                                                    if redis_utils.redis_conn.llen(key_tx_new) > 0:
                                                        list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                                                        if list_new_tx and len(list_new_tx) > 0 and tx['txid'].encode() not in list_new_tx:
                                                            redis_utils.redis_conn.lpush(key_tx_new, tx['txid'])
                                                            redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['address'], 'blockhash': tx['blockhash'], 'amount': tx['amount'], 'decimal': coin_decimal}), ex=86400)
                                                    elif redis_utils.redis_conn.llen(key_tx_new) == 0:
                                                        redis_utils.redis_conn.lpush(key_tx_new, tx['txid'])
                                                        redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['txid'], 'payment_id': tx['address'], 'blockhash': tx['blockhash'], 'amount': tx['amount'], 'decimal': coin_decimal}), ex=86400)
                                                except Exception as e:
                                                    await logchanbot(traceback.format_exc())
                                                    await logchanbot(json.dumps(tx))
                                    # TODO: update balance cache
                        except Exception as e:
                            await logchanbot(traceback.format_exc())
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())



    @tasks.loop(seconds=20.0)
    async def update_balance_chia(self):
        await asyncio.sleep(5.0)
        try:
            list_chia_api = await store.get_coin_settings("CHIA")
            if len(list_chia_api) > 0:
                list_coins = [each['coin_name'].upper() for each in list_chia_api]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    gettopblock = await self.gettopblock(COIN_NAME, time_out=32)
                    height = int(gettopblock['height'])

                    get_confirm_depth = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_confirm_depth")
                    coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                    get_min_deposit_amount = int(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit") * 10**coin_decimal)

                    payload = {'wallet_id': 1}
                    list_tx = await self.call_xch('get_transactions', COIN_NAME, payload=payload)
                    if 'success' in list_tx and list_tx['transactions'] and len(list_tx['transactions']) > 0:
                        get_transfers =  list_tx['transactions']
                        if get_transfers and len(get_transfers) >= 1:
                            try:
                                await store.openConnection()
                                async with store.pool.acquire() as conn:
                                    async with conn.cursor() as cur:
                                        sql = """ SELECT * FROM `xch_get_transfers` WHERE `coin_name` = %s  """
                                        await cur.execute(sql, (COIN_NAME))
                                        result = await cur.fetchall()
                                        d = [i['txid'] for i in result]
                                        # print('=================='+COIN_NAME+'===========')
                                        # print(d)
                                        # print('=================='+COIN_NAME+'===========')
                                        list_balance_user = {}
                                        for tx in get_transfers:
                                            # add to balance only confirmation depth meet
                                            if height >= get_confirm_depth + int(tx['confirmed_at_height']) and tx['amount'] >= get_min_deposit_amount:
                                                if 'to_address' in tx and tx['to_address'] in list_balance_user and tx['amount'] > 0:
                                                    list_balance_user[tx['to_address']] += tx['amount']
                                                elif 'to_address' in tx and tx['to_address'] not in list_balance_user and tx['amount'] > 0:
                                                    list_balance_user[tx['to_address']] = tx['amount']
                                                try:
                                                    if tx['name'] not in d:
                                                        # receive
                                                        if len(tx['sent_to']) == 0:
                                                            sql = """ INSERT IGNORE INTO `xch_get_transfers` (`coin_name`, `txid`, `height`, `timestamp`, `address`, `amount`, `fee`, `decimal`, `time_insert`) 
                                                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) """
                                                            await cur.execute(sql, (COIN_NAME, tx['name'], tx['confirmed_at_height'], tx['created_at_time'],
                                                                                    tx['to_address'], float(tx['amount']/10**coin_decimal), float(tx['fee_amount']/10**coin_decimal), coin_decimal, int(time.time())))
                                                            await conn.commit()
                                                        # add to notification list also, doge payment_id = address
                                                        if (tx['amount'] > 0) and len(tx['sent_to']) == 0:
                                                            sql = """ INSERT IGNORE INTO `discord_notify_new_tx` (`coin_name`, `txid`, `payment_id`, `blockhash`, `height`, `amount`, `fee`, `decimal`) 
                                                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                                                            await cur.execute(sql, (COIN_NAME, tx['name'], tx['to_address'], tx['name'], int(tx['confirmed_at_height']), 
                                                                                    float(tx['amount']/10**coin_decimal), float(tx['fee_amount']/10**coin_decimal), coin_decimal))
                                                            await conn.commit()
                                                except Exception as e:
                                                    traceback.print_exc(file=sys.stdout)
                                                    await logchanbot(traceback.format_exc())
                                            if height < get_confirm_depth + int(tx['confirmed_at_height']) and tx['amount'] >= get_min_deposit_amount:
                                                # add notify to redis and alert deposit. Can be clean later?
                                                if config.notify_new_tx.enable_new_no_confirm == 1:
                                                    key_tx_new = config.redis.prefix_new_tx + 'NOCONFIRM'
                                                    key_tx_json = config.redis.prefix_new_tx + tx['name']
                                                    try:
                                                        if redis_utils.redis_conn.llen(key_tx_new) > 0:
                                                            list_new_tx = redis_utils.redis_conn.lrange(key_tx_new, 0, -1)
                                                            if list_new_tx and len(list_new_tx) > 0 and tx['name'].encode() not in list_new_tx:
                                                                redis_utils.redis_conn.lpush(key_tx_new, tx['name'])
                                                                redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['name'], 'payment_id': tx['to_address'], 'height': tx['confirmed_at_height'], 'amount': float(tx['amount']/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                                        elif redis_utils.redis_conn.llen(key_tx_new) == 0:
                                                            redis_utils.redis_conn.lpush(key_tx_new, tx['name'])
                                                            redis_utils.redis_conn.set(key_tx_json, json.dumps({'coin_name': COIN_NAME, 'txid': tx['name'], 'payment_id': tx['to_address'], 'height': tx['confirmed_at_height'], 'amount': float(tx['amount']/10**coin_decimal), 'decimal': coin_decimal}), ex=86400)
                                                    except Exception as e:
                                                        traceback.print_exc(file=sys.stdout)
                                                        await logchanbot(traceback.format_exc())
                                                        await logchanbot(json.dumps(tx))
                                        # TODO: update balance users
                            except Exception as e:
                                traceback.print_exc(file=sys.stdout)
                                await logchanbot(traceback.format_exc())
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())


    @tasks.loop(seconds=20.0)
    async def update_balance_nano(self):
        await asyncio.sleep(5.0)
        try:
            updated = 0
            list_nano = await store.get_coin_settings("NANO")
            if len(list_nano) > 0:
                list_coins = [each['coin_name'].upper() for each in list_nano]
                for COIN_NAME in list_coins:
                    # print(f"Check balance {COIN_NAME}")
                    start = time.time()
                    timeout = 16
                    try:
                        gettopblock = await self.call_nano(COIN_NAME, payload='{ "action": "block_count" }')
                        if gettopblock and 'count' in gettopblock:
                            height = int(gettopblock['count'])
                            # store in redis
                            try:                                
                                redis_utils.redis_conn.set(f'{config.redis.prefix + config.redis.daemon_height}{COIN_NAME}', str(height))
                            except Exception as e:
                                traceback.print_exc(file=sys.stdout)
                                await logchanbot(traceback.format_exc())
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
                        await logchanbot(traceback.format_exc())
                    get_balance = await self.nano_get_wallet_balance_elements(COIN_NAME)
                    all_user_info = await store.sql_nano_get_user_wallets(COIN_NAME)
                    all_deposit_address = {}
                    all_deposit_address_keys = []
                    if len(all_user_info) > 0:
                        all_deposit_address_keys = [each['balance_wallet_address'] for each in all_user_info]
                        for each in all_user_info:
                            all_deposit_address[each['balance_wallet_address']] = each
                    if get_balance and len(get_balance) > 0:
                        for address, balance in get_balance.items():
                            try:
                                # if bigger than minimum deposit, and no pending and the address is in user database addresses
                                real_min_deposit = getattr(getattr(self.bot.coin_list, COIN_NAME), "real_min_deposit")
                                coin_decimal = getattr(getattr(self.bot.coin_list, COIN_NAME), "decimal")
                                if float(int(balance['balance'])/10**coin_decimal) >= real_min_deposit and float(int(balance['pending'])/10**coin_decimal) == 0 and address in all_deposit_address_keys:
                                    # let's move balance to main_address
                                    try:
                                        main_address = getattr(getattr(self.bot.coin_list, COIN_NAME), "MainAddress")
                                        move_to_deposit = await self.nano_sendtoaddress(address, main_address, int(balance['balance']), COIN_NAME) # atomic
                                        # add to DB
                                        if move_to_deposit:
                                            try:
                                                await store.openConnection()
                                                async with store.pool.acquire() as conn:
                                                    async with conn.cursor() as cur:
                                                        sql = """ INSERT INTO nano_move_deposit (`coin_name`, `user_id`, `balance_wallet_address`, `to_main_address`, `amount`, `decimal`, `block`, `time_insert`) 
                                                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                                                        await cur.execute(sql, (COIN_NAME, all_deposit_address[address]['user_id'], address, main_address, float(int(balance['balance'])/10**coin_decimal), coin_decimal, move_to_deposit['block'], int(time.time()), ))
                                                        await conn.commit()
                                                        updated += 1
                                                        # add to notification list also
                                                        # txid = new block ID
                                                        # payment_id = deposit address
                                                        sql = """ INSERT IGNORE INTO discord_notify_new_tx (`coin_name`, `txid`, `payment_id`, `amount`, `decimal`) 
                                                                  VALUES (%s, %s, %s, %s, %s) """
                                                        await cur.execute(sql, (COIN_NAME, move_to_deposit['block'], address, float(int(balance['balance'])/10**coin_decimal), coin_decimal,))
                                                        await conn.commit()
                                            except Exception as e:
                                                traceback.print_exc(file=sys.stdout)
                                                await logchanbot(traceback.format_exc())
                                    except Exception as e:
                                        traceback.print_exc(file=sys.stdout)
                                        await logchanbot(traceback.format_exc())
                            except Exception as e:
                                traceback.print_exc(file=sys.stdout)
                                await logchanbot(traceback.format_exc())
                    end = time.time()
                    # print('Done update balance: '+ COIN_NAME+ ' updated *'+str(updated)+'* duration (s): '+str(end - start))
                    await asyncio.sleep(4.0)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        await asyncio.sleep(5.0)


    async def create_address_eth(self):
        def create_eth_wallet():
            seed = ethwallet.generate_mnemonic()
            w = ethwallet.create_wallet(network="ETH", seed=seed, children=1)
            return w

        wallet_eth = functools.partial(create_eth_wallet)
        create_wallet = await self.bot.loop.run_in_executor(None, wallet_eth)
        return create_wallet


    async def create_address_trx(self):
        try:
            _http_client = AsyncClient(limits=Limits(max_connections=100, max_keepalive_connections=20),
                                       timeout=Timeout(timeout=10, connect=5, read=5))
            TronClient = AsyncTron(provider=AsyncHTTPProvider(config.Tron_Node.fullnode, client=_http_client))
            create_wallet = TronClient.generate_address()
            await TronClient.close()
            return create_wallet
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


    async def call_aiohttp_wallet_xmr_bcn(self, method_name: str, coin: str, time_out: int = None, payload: Dict = None) -> Dict:
        COIN_NAME = coin.upper()
        coin_family = getattr(getattr(self.bot.coin_list, COIN_NAME), "type")
        full_payload = {
            'params': payload or {},
            'jsonrpc': '2.0',
            'id': str(uuid.uuid4()),
            'method': f'{method_name}'
        }
        url = getattr(getattr(self.bot.coin_list, COIN_NAME), "wallet_address")
        timeout = time_out or 60
        if method_name == "save" or method_name == "store":
            timeout = 300
        elif method_name == "sendTransaction":
            timeout = 180
        elif method_name == "createAddress" or method_name == "getSpendKeys":
            timeout = 60
        try:
            if COIN_NAME == "LTHN":
                # Copied from XMR below
                try:
                    async with aiohttp.ClientSession(headers={'Content-Type': 'application/json'}) as session:
                        async with session.post(url, json=full_payload, timeout=timeout) as response:
                            # sometimes => "message": "Not enough unlocked money" for checking fee
                            if method_name == "split_integrated_address":
                                # we return all data including error
                                if response.status == 200:
                                    res_data = await response.read()
                                    res_data = res_data.decode('utf-8')
                                    decoded_data = json.loads(res_data)
                                    return decoded_data
                            elif method_name == "transfer":
                                print('{} - transfer'.format(COIN_NAME))

                            if response.status == 200:
                                res_data = await response.read()
                                res_data = res_data.decode('utf-8')
                                if method_name == "transfer":
                                    print(res_data)
                                
                                decoded_data = json.loads(res_data)
                                if 'result' in decoded_data:
                                    return decoded_data['result']
                                else:
                                    print(decoded_data)
                                    return None
                except asyncio.TimeoutError:
                    await logchanbot('call_aiohttp_wallet: method_name: {} COIN_NAME {} - timeout {}\nfull_payload:\n{}'.format(method_name, COIN_NAME, timeout, json.dumps(payload)))
                    print('TIMEOUT: {} COIN_NAME {} - timeout {}'.format(method_name, COIN_NAME, timeout))
                    return None
                except Exception:
                    await logchanbot(traceback.format_exc())
                    return None
            elif coin_family == "XMR":
                try:
                    async with aiohttp.ClientSession(headers={'Content-Type': 'application/json'}) as session:
                        async with session.post(url, json=full_payload, timeout=timeout) as response:
                            # sometimes => "message": "Not enough unlocked money" for checking fee
                            if method_name == "transfer":
                                print('{} - transfer'.format(COIN_NAME))
                                print(full_payload)
                            if response.status == 200:
                                res_data = await response.read()
                                res_data = res_data.decode('utf-8')
                                if method_name == "transfer":
                                    print(res_data)
                                
                                decoded_data = json.loads(res_data)
                                if 'result' in decoded_data:
                                    return decoded_data['result']
                                else:
                                    return None
                except asyncio.TimeoutError:
                    await logchanbot('call_aiohttp_wallet: method_name: {} COIN_NAME {} - timeout {}\nfull_payload:\n{}'.format(method_name, COIN_NAME, timeout, json.dumps(payload)))
                    print('TIMEOUT: {} COIN_NAME {} - timeout {}'.format(method_name, COIN_NAME, timeout))
                    return None
                except Exception:
                    await logchanbot(traceback.format_exc())
                    return None
            elif coin_family in ["TRTL-SERVICE", "BCN"]:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=full_payload, timeout=timeout) as response:
                            if response.status == 200 or response.status == 201:
                                res_data = await response.read()
                                res_data = res_data.decode('utf-8')
                                
                                decoded_data = json.loads(res_data)
                                if 'result' in decoded_data:
                                    return decoded_data['result']
                                else:
                                    await logchanbot(str(res_data))
                                    return None
                            else:
                                await logchanbot(str(response))
                                return None
                except asyncio.TimeoutError:
                    await logchanbot('call_aiohttp_wallet: {} COIN_NAME {} - timeout {}\nfull_payload:\n{}'.format(method_name, COIN_NAME, timeout, json.dumps(payload)))
                    print('TIMEOUT: {} COIN_NAME {} - timeout {}'.format(method_name, COIN_NAME, timeout))
                    return None
                except Exception:
                    traceback.print_exc(file=sys.stdout)
                    await logchanbot(traceback.format_exc())
                    return None
        except asyncio.TimeoutError:
            await logchanbot('call_aiohttp_wallet: method_name: {} - coin_family: {} - timeout {}'.format(method_name, coin_family, timeout))
            print('TIMEOUT: method_name: {} - coin_family: {} - timeout {}'.format(method_name, coin_family, timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())


    async def make_integrated_address_xmr(self, address: str, coin: str, paymentid: str = None):
        COIN_NAME = coin.upper()
        if paymentid:
            try:
                value = int(paymentid, 16)
            except ValueError:
                return False
        else:
            paymentid = cn_addressvalidation.paymentid(8)

        if COIN_NAME == "LTHN":
            payload = {
                "payment_id": {} or paymentid
            }
            address_ia = await self.call_aiohttp_wallet_xmr_bcn('make_integrated_address', COIN_NAME, payload=payload)
            if address_ia: return address_ia
            return None
        else:
            payload = {
                "standard_address" : address,
                "payment_id": {} or paymentid
            }
            address_ia = await self.call_aiohttp_wallet_xmr_bcn('make_integrated_address', COIN_NAME, payload=payload)
            if address_ia: return address_ia
            return None


    async def call_nano(self, coin: str, payload: str) -> Dict:
        timeout = 100
        COIN_NAME = coin.upper()
        url = getattr(getattr(self.bot.coin_list, COIN_NAME), "rpchost")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload, timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        decoded_data = json.loads(res_data)
                        return decoded_data
        except asyncio.TimeoutError:
            print('TIMEOUT: COIN: {} - timeout {}'.format(coin.upper(), timeout))
            await logchanbot('TIMEOUT: call_nano COIN: {} - timeout {}'.format(coin.upper(), timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())
        return None


    async def nano_get_wallet_balance_elements(self, coin: str) -> str:
        COIN_NAME = coin.upper()
        walletkey = decrypt_string(getattr(getattr(self.bot.coin_list, COIN_NAME), "walletkey"))
        get_wallet_balance = await self.call_nano(COIN_NAME, payload='{ "action": "wallet_balances", "wallet": "'+walletkey+'" }')
        if get_wallet_balance and 'balances' in get_wallet_balance:
            return get_wallet_balance['balances']
        return None

    async def nano_sendtoaddress(self, source: str, to_address: str, atomic_amount: int, coin: str) -> str:
        COIN_NAME = coin.upper()
        walletkey = decrypt_string(getattr(getattr(self.bot.coin_list, COIN_NAME), "walletkey"))
        payload = '{ "action": "send", "wallet": "'+walletkey+'", "source": "'+source+'", "destination": "'+to_address+'", "amount": "'+str(atomic_amount)+'" }'
        sending = await self.call_nano(COIN_NAME, payload=payload)
        if sending and 'block' in sending:
            return sending
        return None


    async def call_xch(self, method_name: str, coin: str, payload: Dict=None) -> Dict:
        timeout = 100
        COIN_NAME = coin.upper()

        headers = {
            'Content-Type': 'application/json',
        }
        if payload is None:
            data = '{}'
        else:
            data = payload
        url = getattr(getattr(self.bot.coin_list, COIN_NAME), "rpchost") + '/' + method_name.lower()
        try:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(getattr(getattr(self.bot.coin_list, COIN_NAME), "cert_path"), getattr(getattr(self.bot.coin_list, COIN_NAME), "key_path"))
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
                async with session.post(url, json=data, headers=headers, timeout=timeout, ssl=ssl_context) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        
                        decoded_data = json.loads(res_data)
                        return decoded_data
                    else:
                        await logchanbot(f'Call {COIN_NAME} returns {str(response.status)} with method {method_name}')
        except asyncio.TimeoutError:
            print('TIMEOUT: method_name: {} - COIN: {} - timeout {}'.format(method_name, COIN_NAME, timeout))
            await logchanbot('call_doge: method_name: {} - COIN: {} - timeout {}'.format(method_name, COIN_NAME, timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await logchanbot(traceback.format_exc())


    async def call_doge(self, method_name: str, coin: str, payload: str = None) -> Dict:
        timeout = 100
        COIN_NAME = coin.upper()
        headers = {
            'content-type': 'text/plain;',
        }
        if payload is None:
            data = '{"jsonrpc": "1.0", "id":"'+str(uuid.uuid4())+'", "method": "'+method_name+'", "params": [] }'
        else:
            data = '{"jsonrpc": "1.0", "id":"'+str(uuid.uuid4())+'", "method": "'+method_name+'", "params": ['+payload+'] }'
        
        url = getattr(getattr(self.bot.coin_list, COIN_NAME), "daemon_address")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.read()
                        res_data = res_data.decode('utf-8')
                        
                        decoded_data = json.loads(res_data)
                        return decoded_data['result']
                    else:
                        await logchanbot(f'Call {COIN_NAME} returns {str(response.status)} with method {method_name}')
        except asyncio.TimeoutError:
            print('TIMEOUT: method_name: {} - COIN: {} - timeout {}'.format(method_name, coin.upper(), timeout))
            await logchanbot('call_doge: method_name: {} - COIN: {} - timeout {}'.format(method_name, coin.upper(), timeout))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


    async def trtl_api_get_transfers(self, url: str, key: str, coin: str, height_start: int = None, height_end: int = None):
        time_out = 30
        method = "/transactions"
        headers = {
            'X-API-KEY': key,
            'Content-Type': 'application/json'
        }
        if (height_start is None) or (height_end is None):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url + method, headers=headers, timeout=time_out) as response:
                        json_resp = await response.json()
                        if response.status == 200 or response.status == 201:
                            return json_resp['transactions']
                        elif 'errorMessage' in json_resp:
                            raise RPCException(json_resp['errorMessage'])
            except asyncio.TimeoutError:
                await logchanbot('trtl_api_get_transfers: TIMEOUT: {} - coin {} timeout {}'.format(method, coin, time_out))
            except Exception as e:
                await logchanbot('trtl_api_get_transfers: '+ str(traceback.format_exc()))
        elif height_start and height_end:
            method += '/' + str(height_start) + '/' + str(height_end)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url + method, headers=headers, timeout=time_out) as response:
                        json_resp = await response.json()
                        if response.status == 200 or response.status == 201:
                            return json_resp['transactions']
                        elif 'errorMessage' in json_resp:
                            raise RPCException(json_resp['errorMessage'])
            except asyncio.TimeoutError:
                await logchanbot('trtl_api_get_transfers: TIMEOUT: {} - coin {} timeout {}'.format(method, coin, time_out))
            except Exception as e:
                await logchanbot('trtl_api_get_transfers: ' + str(traceback.format_exc()))


    async def trtl_service_getTransactions(self, url: str, coin: str, firstBlockIndex: int=2000000, blockCount: int= 200000):
        COIN_NAME = coin.upper()
        time_out = 64
        payload = {
            'firstBlockIndex': firstBlockIndex if firstBlockIndex > 0 else 1,
            'blockCount': blockCount,
            }
        result = await self.call_aiohttp_wallet_xmr_bcn('getTransactions', COIN_NAME, time_out=time_out, payload=payload)
        if result and 'items' in result:
            return result['items']
        return []


    # Mostly for BCN/XMR
    async def call_daemon(self, get_daemon_rpc_url: str, method_name: str, coin: str, time_out: int = None, payload: Dict = None) -> Dict:
        full_payload = {
            'params': payload or {},
            'jsonrpc': '2.0',
            'id': str(uuid.uuid4()),
            'method': f'{method_name}'
        }
        timeout = time_out or 16
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(get_daemon_rpc_url + '/json_rpc', json=full_payload, timeout=timeout) as response:
                    if response.status == 200:
                        res_data = await response.json()
                        if res_data and 'result' in res_data:
                            return res_data['result']
                        else:
                            return res_data
        except asyncio.TimeoutError:
            await logchanbot('call_daemon: method: {} COIN_NAME {} - timeout {}'.format(method_name, coin.upper(), time_out))
            return None
        except Exception:
            traceback.print_exc(file=sys.stdout)
            return None


    async def gettopblock(self, coin: str, time_out: int = None):
        COIN_NAME = coin.upper()
        coin_family = getattr(getattr(self.bot.coin_list, COIN_NAME), "type")
        get_daemon_rpc_url = getattr(getattr(self.bot.coin_list, COIN_NAME), "daemon_address")
        result = None
        timeout = time_out or 32

        if COIN_NAME in ["LTHN"] or coin_family in ["BCN", "TRTL-API", "TRTL-SERVICE"]:
            method_name = "getblockcount"
            full_payload = {
                'params': {},
                'jsonrpc': '2.0',
                'id': str(uuid.uuid4()),
                'method': f'{method_name}'
            }
            try:
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(get_daemon_rpc_url + '/json_rpc', json=full_payload, timeout=timeout) as response:
                        if response.status == 200:
                            res_data = await response.json()
                            result = None
                            if res_data and 'result' in res_data:
                                result = res_data['result']
                            else:
                                result = res_data
                            if result:
                                full_payload = {
                                    'jsonrpc': '2.0',
                                    'method': 'getblockheaderbyheight',
                                    'params': {'height': result['count'] - 1}
                                }
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(get_daemon_rpc_url + '/json_rpc', json=full_payload, timeout=timeout) as response:
                                            if response.status == 200:
                                                res_data = await response.json()
                                                return res_data['result']
                                except asyncio.TimeoutError:
                                    traceback.print_exc(file=sys.stdout)
                                except Exception:
                                    traceback.print_exc(file=sys.stdout)
                            return None
            except asyncio.TimeoutError:
                await logchanbot('gettopblock: method: {} COIN_NAME {} - timeout {}'.format(method_name, coin.upper(), time_out))
            except Exception:
                traceback.print_exc(file=sys.stdout)
            return None
        elif coin_family == "XMR":
            method_name = "get_block_count"
            full_payload = {
                'params': {},
                'jsonrpc': '2.0',
                'id': str(uuid.uuid4()),
                'method': f'{method_name}'
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(get_daemon_rpc_url + '/json_rpc', json=full_payload, timeout=timeout) as response:
                        if response.status == 200:
                            try:
                                res_data = await response.json()
                            except Exception:
                                res_data = await response.read()
                                res_data = res_data.decode('utf-8')
                                res_data = json.loads(res_data)
                            result = None
                            if res_data and 'result' in res_data:
                                result = res_data['result']
                            else:
                                result = res_data
                            if result:
                                full_payload = {
                                    'jsonrpc': '2.0',
                                    'method': 'get_block_header_by_height',
                                    'params': {'height': result['count'] - 1}
                                }
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(get_daemon_rpc_url + '/json_rpc', json=full_payload, timeout=timeout) as response:
                                            if response.status == 200:
                                                res_data = await response.json()
                                                if res_data and 'result' in res_data:
                                                    return res_data['result']
                                                else:
                                                    return res_data
                                except asyncio.TimeoutError:
                                    await logchanbot('gettopblock: method: {} COIN_NAME {} - timeout {}'.format('get_block_count', COIN_NAME, time_out))
                                except Exception:
                                    traceback.print_exc(file=sys.stdout)
                            return None
            except asyncio.TimeoutError:
                await logchanbot('gettopblock: method: {} COIN_NAME {} - timeout {}'.format(method_name, coin.upper(), time_out))
            except Exception:
                traceback.print_exc(file=sys.stdout)
            return None
        elif coin_family == "CHIA":
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(getattr(getattr(self.bot.coin_list, COIN_NAME), "cert_path"), getattr(getattr(self.bot.coin_list, COIN_NAME), "key_path"))
            url = getattr(getattr(self.bot.coin_list, COIN_NAME), "daemon_address") + '/get_blockchain_state'
            try:
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
                    async with session.post(url, timeout=timeout, json={}, ssl=ssl_context) as response:
                        if response.status == 200:
                            res_data = await response.json()
                            return res_data['blockchain_state']['peak']
            except asyncio.TimeoutError:
                await logchanbot('gettopblock: method: {} COIN_NAME {} - timeout {}'.format("get_blockchain_state", coin.upper(), time_out))
            except Exception:
                traceback.print_exc(file=sys.stdout)
            return None


    async def sql_get_userwallet(self, userID, coin: str, netname: str, type_coin: str, user_server: str = 'DISCORD', chat_id: int = 0):
        # type_coin: 'ERC-20','TRC-20','TRTL-API','TRTL-SERVICE','BCN','XMR','NANO','BTC','CHIA','OTHER'
        # netname null or None, xDai, MATIC, TRX, BSC
        user_server = user_server.upper()
        COIN_NAME = coin.upper()
        if type_coin.upper() == "ERC-20" and COIN_NAME != netname.upper():
            user_id_erc20 = str(userID) + "_" + type_coin.upper()
        elif type_coin.upper() == "ERC-20" and COIN_NAME == netname.upper():
            user_id_erc20 = str(userID) + "_" + COIN_NAME
        if type_coin.upper() == "TRC-20" and COIN_NAME != netname.upper():
            user_id_erc20 = str(userID) + "_" + type_coin.upper()
        elif type_coin.upper() == "TRC-20" and COIN_NAME == netname.upper():
            user_id_erc20 = str(userID) + "_" + COIN_NAME
        try:
            await store.openConnection()
            async with store.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    if netname and netname not in ["TRX"]:
                        sql = """ SELECT * FROM `erc20_user` WHERE `user_id`=%s 
                                  AND `user_id_erc20`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), user_id_erc20, user_server))
                        result = await cur.fetchone()
                        if result: return result
                    elif netname and netname in ["TRX"]:
                        sql = """ SELECT * FROM `trc20_user` WHERE `user_id`=%s 
                                  AND `user_id_trc20`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), user_id_erc20, user_server))
                        result = await cur.fetchone()
                        if result: return result
                    elif type_coin.upper() in ["TRTL-API", "TRTL-SERVICE", "BCN", "XMR"]:
                        sql = """ SELECT * FROM `cn_user_paymentid` WHERE `user_id`=%s 
                                  AND `coin_name`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                        result = await cur.fetchone()
                        if result: return result
                    elif type_coin.upper() == "NANO":
                        sql = """ SELECT * FROM `nano_user` WHERE `user_id`=%s 
                                  AND `coin_name`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                        result = await cur.fetchone()
                        if result: return result
                    elif type_coin.upper() == "BTC":
                        sql = """ SELECT * FROM `doge_user` WHERE `user_id`=%s 
                                  AND `coin_name`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                        result = await cur.fetchone()
                        if result: return result
                    elif type_coin.upper() == "CHIA":
                        sql = """ SELECT * FROM `xch_user` WHERE `user_id`=%s 
                                  AND `coin_name`=%s AND `user_server` = %s LIMIT 1 """
                        await cur.execute(sql, (str(userID), COIN_NAME, user_server))
                        result = await cur.fetchone()
                        if result: return result
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        return None


    # TODO: all coin, to register
    # ERC-20, TRC-20, native is one
    # Gas Token like BNB, xDAI, MATIC, TRX will be a different address
    async def sql_register_user(self, userID, coin: str, netname: str, type_coin: str, user_server: str, chat_id: int = 0):
        try:
            COIN_NAME = coin.upper()
            user_server = user_server.upper()
            balance_address = None
            main_address = None

            if type_coin.upper() == "ERC-20" and COIN_NAME != netname.upper():
                user_id_erc20 = str(userID) + "_" + type_coin.upper()
            elif type_coin.upper() == "ERC-20" and COIN_NAME == netname.upper():
                user_id_erc20 = str(userID) + "_" + COIN_NAME
            if type_coin.upper() == "TRC-20" and COIN_NAME != netname.upper():
                user_id_erc20 = str(userID) + "_" + type_coin.upper()
            elif type_coin.upper() == "TRC-20" and COIN_NAME == netname.upper():
                user_id_erc20 = str(userID) + "_" + COIN_NAME

            if type_coin.upper() == "ERC-20":
                # passed test XDAI, MATIC
                w = await self.create_address_eth()
                balance_address = w['address']
            elif type_coin.upper() == "TRC-20":
                # passed test TRX, USDT
                w = await self.create_address_trx()
                balance_address = w
            elif type_coin.upper() in ["TRTL-API", "TRTL-SERVICE", "BCN"]:
                # passed test WRKZ, DEGO
                main_address = getattr(getattr(self.bot.coin_list, COIN_NAME), "MainAddress")
                get_prefix_char = getattr(getattr(self.bot.coin_list, COIN_NAME), "get_prefix_char")
                get_prefix = getattr(getattr(self.bot.coin_list, COIN_NAME), "get_prefix")
                get_addrlen = getattr(getattr(self.bot.coin_list, COIN_NAME), "get_addrlen")
                balance_address = {}
                balance_address['payment_id'] = cn_addressvalidation.paymentid()
                balance_address['integrated_address'] = cn_addressvalidation.cn_make_integrated(main_address, get_prefix_char, get_prefix, get_addrlen, balance_address['payment_id'])['integrated_address']
            elif type_coin.upper() == "XMR":
                # passed test WOW
                main_address = getattr(getattr(self.bot.coin_list, COIN_NAME), "MainAddress")
                balance_address = await self.make_integrated_address_xmr(main_address, COIN_NAME)
            elif type_coin.upper() == "NANO":
                walletkey = decrypt_string(getattr(getattr(self.bot.coin_list, COIN_NAME), "walletkey"))
                balance_address = await self.call_nano(COIN_NAME, payload='{ "action": "account_create", "wallet": "'+walletkey+'" }')
            elif type_coin.upper() == "BTC":
                # passed test PGO, XMY
                naming = config.redis.prefix + "_"+user_server+"_" + str(userID)
                payload = f'"{naming}"'
                address_call = await self.call_doge('getnewaddress', COIN_NAME, payload=payload)
                reg_address = {}
                reg_address['address'] = address_call
                payload = f'"{address_call}"'
                key_call = await self.call_doge('dumpprivkey', COIN_NAME, payload=payload)
                reg_address['privateKey'] = key_call
                if reg_address['address'] and reg_address['privateKey']:
                    balance_address = reg_address
            elif type_coin.upper() == "CHIA":
                # passed test XFX
                payload = {'wallet_id': 1, 'new_address': True}
                try:
                    address_call = await self.call_xch('get_next_address', COIN_NAME, payload=payload)
                    if 'success' in address_call and address_call['address']:
                        balance_address = address_call
                except Exception as e:
                    traceback.print_exc(file=sys.stdout)

            await store.openConnection()
            async with store.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    try:
                        if netname and netname not in ["TRX"]:
                            sql = """ INSERT INTO `erc20_user` (`user_id`, `user_id_erc20`, `balance_wallet_address`, `address_ts`, 
                                      `seed`, `create_dump`, `private_key`, `public_key`, `xprivate_key`, `xpublic_key`, 
                                      `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (str(userID), user_id_erc20, w['address'], int(time.time()), 
                                              encrypt_string(w['seed']), encrypt_string(str(w)), encrypt_string(str(w['private_key'])), w['public_key'], 
                                              encrypt_string(str(w['xprivate_key'])), w['xpublic_key'], user_server))
                            await conn.commit()
                            return {'balance_wallet_address': w['address']}
                        elif netname and netname in ["TRX"]:
                            sql = """ INSERT INTO `trc20_user` (`user_id`, `user_id_trc20`, `balance_wallet_address`, `hex_address`, `address_ts`, 
                                      `private_key`, `public_key`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (str(userID), user_id_erc20, w['base58check_address'], w['hex_address'], int(time.time()), 
                                              encrypt_string(str(w['private_key'])), w['public_key'], user_server))
                            await conn.commit()
                            return {'balance_wallet_address': w['base58check_address']}
                        elif type_coin.upper() in ["TRTL-API", "TRTL-SERVICE", "BCN", "XMR"]:
                            sql = """ INSERT INTO cn_user_paymentid (`coin_name`, `user_id`, `user_id_coin`, `main_address`, `paymentid`, 
                                      `balance_wallet_address`, `paymentid_ts`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), "{}_{}".format(userID, COIN_NAME), main_address, balance_address['payment_id'], 
                                                    balance_address['integrated_address'], int(time.time()), user_server))
                            await conn.commit()
                            return {'balance_wallet_address': balance_address['integrated_address']}
                        elif type_coin.upper() == "NANO":
                            sql = """ INSERT INTO `nano_user` (`coin_name`, `user_id`, `user_id_coin`, `balance_wallet_address`, `address_ts`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), "{}_{}".format(userID, COIN_NAME), balance_address['account'], int(time.time()), user_server))
                            await conn.commit()
                            return {'balance_wallet_address': balance_address['account']}
                        elif type_coin.upper() == "BTC":
                            sql = """ INSERT INTO `doge_user` (`coin_name`, `user_id`, `user_id_coin`, `balance_wallet_address`, `address_ts`, `privateKey`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), "{}_{}".format(userID, COIN_NAME), balance_address['address'], int(time.time()), 
                                                    encrypt_string(balance_address['privateKey']), user_server))
                            await conn.commit()
                            return {'balance_wallet_address': balance_address['address']}
                        elif type_coin.upper() == "CHIA":
                            sql = """ INSERT INTO `xch_user` (`coin_name`, `user_id`, `user_id_coin`, `balance_wallet_address`, `address_ts`, `user_server`) 
                                      VALUES (%s, %s, %s, %s, %s, %s) """
                            await cur.execute(sql, (COIN_NAME, str(userID), "{}_{}".format(userID, COIN_NAME), balance_address['address'], int(time.time()), user_server))
                            await conn.commit()
                            return {'balance_wallet_address': balance_address['address']}
                    except Exception as e:
                        traceback.print_exc(file=sys.stdout)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
        return None


    async def generate_qr_address(
        self, 
        address: str
    ):
        # return path to image
        # address = wallet['balance_wallet_address']
        # return address if success, else None
        if not os.path.exists(config.storage.path_deposit_qr_create + address + ".png"):
            try:
                # do some QR code
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=2,
                )
                qr.add_data(address)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                img = img.resize((256, 256))
                img.save(config.storage.path_deposit_qr_create + address + ".png")
                return address
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
                await logchanbot(traceback.format_exc())
        else:
            return address
        return None


    async def async_deposit(self, ctx, token: str=None, plain: str=None):
        COIN_NAME = None
        if token is None:
            if type(ctx) == disnake.ApplicationCommandInteraction:
                await ctx.response.send_message(f'{ctx.author.mention}, token name is missing.')
            else:
                await ctx.reply(f'{ctx.author.mention}, token name is missing.')
            return
        else:
            COIN_NAME = token.upper()
            # print(self.bot.coin_list)
            if not hasattr(self.bot.coin_list, COIN_NAME):
                if type(ctx) == disnake.ApplicationCommandInteraction:
                    await ctx.response.send_message(f'{ctx.author.mention}, **{COIN_NAME}** does not exist with us.')
                else:
                    await ctx.reply(f'{ctx.author.mention}, **{COIN_NAME}** does not exist with us.')
                return
            else:
                if getattr(getattr(self.bot.coin_list, COIN_NAME), "enable_deposit") == 0:
                    if type(ctx) == disnake.ApplicationCommandInteraction:
                        await ctx.response.send_message(f'{ctx.author.mention}, **{COIN_NAME}** deposit disable.')
                    else:
                        await ctx.reply(f'{ctx.author.mention}, **{COIN_NAME}** deposit disable.')
                    return
                    
        # Do the job
        try:
            netname = getattr(getattr(self.bot.coin_list, COIN_NAME), "net_name")
            type_coin = getattr(getattr(self.bot.coin_list, COIN_NAME), "type")
            get_deposit = await self.sql_get_userwallet(str(ctx.author.id), COIN_NAME, netname, type_coin, SERVER_BOT, 0)
            if get_deposit is None:
                get_deposit = await self.sql_register_user(str(ctx.author.id), COIN_NAME, netname, type_coin, SERVER_BOT, 0)
                
            wallet_address = get_deposit['balance_wallet_address']
            description = ""
            fee_txt = ""
            token_display = getattr(getattr(self.bot.coin_list, COIN_NAME), "display_name")
            if getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_note") and len(getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_note")) > 0:
                description = getattr(getattr(self.bot.coin_list, COIN_NAME), "deposit_note")
            if getattr(getattr(self.bot.coin_list, COIN_NAME), "real_deposit_fee") and getattr(getattr(self.bot.coin_list, COIN_NAME), "real_deposit_fee") > 0:
                fee_txt = " **{} {}** will be deducted from your deposit when it reaches minimum.".format(getattr(getattr(self.bot.coin_list, COIN_NAME), "real_deposit_fee"), token_display)
            embed = disnake.Embed(title=f'Deposit for {ctx.author.name}#{ctx.author.discriminator}', description=description + fee_txt, timestamp=datetime.utcnow())
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar)
            try:
                gen_qr_address = await self.generate_qr_address(wallet_address)
                embed.set_thumbnail(url=config.storage.deposit_url + wallet_address + ".png")
            except Exception as e:
                traceback.print_exc(file=sys.stdout)
            plain_msg = '{}#{} Your deposit address: ```{}```'.format(ctx.author.name, ctx.author.discriminator, wallet_address)
            embed.add_field(name="Your Deposit Address", value="`{}`".format(wallet_address), inline=False)
            if getattr(getattr(self.bot.coin_list, COIN_NAME), "explorer_link") and len(getattr(getattr(self.bot.coin_list, COIN_NAME), "explorer_link")) > 0:
                embed.add_field(name="Other links", value="[{}]({})".format("Explorer", getattr(getattr(self.bot.coin_list, COIN_NAME), "explorer_link")), inline=False)
            embed.set_footer(text="Use: deposit plain (for plain text)")
            try:
                # Try DM first
                if type(ctx) == disnake.ApplicationCommandInteraction:
                    if plain and plain.lower() == 'plain' or plain.lower() == 'text':
                        await ctx.response.send_message(plain_msg, ephemeral=True)
                    else:
                        await ctx.response.send_message(embed=embed, ephemeral=True)
                else:
                    if plain and plain.lower() == 'plain' or plain.lower() == 'text':
                        msg = await ctx.reply(plain_msg, view=RowButton_close_message())
                        await store.add_discord_bot_message(str(msg.id), "DM" if isinstance(ctx.channel, disnake.DMChannel) else str(ctx.guild.id), str(ctx.author.id))
                    else:
                        msg = await ctx.reply(embed=embed, view=RowButton_close_message())
                        await store.add_discord_bot_message(str(msg.id), "DM" if isinstance(ctx.channel, disnake.DMChannel) else str(ctx.guild.id), str(ctx.author.id))
            except (disnake.Forbidden, disnake.errors.Forbidden) as e:
                traceback.print_exc(file=sys.stdout)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)


    @commands.command(
        usage='deposit <token> [plain/embed]', 
        aliases=['deposit'],
        description="Get your wallet deposit address."
    )
    async def _deposit(
        self, 
        ctx, 
        token: str,
        plain: str = 'embed'
    ):
        await self.async_deposit(ctx, token, plain)


    @commands.slash_command(
        usage='deposit <token> [plain/embed]', 
        options=[
            Option('token', 'token', OptionType.string, required=True),
            Option('plain', 'plain', OptionType.string, required=False)
        ],
        description="Get your wallet deposit address."
    )
    async def deposit(
        self, 
        ctx, 
        token: str,
        plain: str = 'embed'
    ):
        await self.async_deposit(ctx, token, plain)


def setup(bot):
    bot.add_cog(Wallet(bot))