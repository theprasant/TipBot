import sys, traceback
import time, timeago
import discord
from discord.ext import commands
from dislash import InteractionClient, ActionRow, Button, ButtonStyle, Option, OptionType, OptionChoice
import dislash

from config import config
from Bot import *


class TipTipAll(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.botLogChan = self.bot.get_channel(LOG_CHAN)


    async def bot_log(self):
        if self.botLogChan is None:
            self.botLogChan = self.bot.get_channel(LOG_CHAN)


    async def tip_all(
        self,
        ctx,
        amount, 
        coin: str, 
        option: str
    ):
        await self.bot_log()
        # check if bot is going to restart
        if IS_RESTARTING: return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Bot is going to restart soon. Wait until it is back for using this."}
        # check if account locked
        account_lock = await alert_if_userlock(ctx, 'tipall')
        if account_lock:
            return {"error": f"{EMOJI_RED_NO} {MSG_LOCKED_ACCOUNT}"}
        # end of check if account locked

        # TRTL discord
        if ctx.guild.id == TRTL_DISCORD and COIN_NAME != "TRTL":
            return {"error": f"{EMOJI_ERROR} {ctx.author.mention}, Not available for this coin in this guild."}
            
        # Check if tx in progress
        if ctx.author.id in TX_IN_PROCESS:
            return {"error": f"{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress."}

        COIN_NAME = coin.upper()
        if COIN_NAME not in ENABLE_COIN+ENABLE_COIN_DOGE+ENABLE_XMR+ENABLE_COIN_NANO+ENABLE_COIN_ERC+ENABLE_COIN_TRC+ENABLE_XCH:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} **INVALID TICKER**!"}

        serverinfo = await store.sql_info_by_server(str(ctx.guild.id))
        if COIN_NAME in ENABLE_COIN_ERC:
            coin_family = "ERC-20"
        elif COIN_NAME in ENABLE_COIN_TRC:
            coin_family = "TRC-20"
        else:
            coin_family = getattr(getattr(config,"daemon"+COIN_NAME),"coin_family","TRTL")

        option = option.upper() if option else "ONLINE"
        option_list = ["ALL", "ONLINE"]
        if option not in option_list:
            allow_option = ", ".join(option_list)
            return {"error": f"{EMOJI_ERROR} {ctx.author.mention} TIPALL is currently support only option **{allow_option}**."}

        if not is_coin_tipable(COIN_NAME):
            return {"error": f"{EMOJI_ERROR} {ctx.author.mention} TIPPING is currently disable for {COIN_NAME}."}

        if is_maintenance_coin(COIN_NAME):
            return {"error": f"{EMOJI_RED_NO} {COIN_NAME} in maintenance."}

        # Check allowed coins
        tiponly_coins = serverinfo['tiponly'].split(",")
        if COIN_NAME == serverinfo['default_coin'].upper() or serverinfo['tiponly'].upper() == "ALLCOIN":
            pass
        elif COIN_NAME not in tiponly_coins:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} {COIN_NAME} not in allowed coins set by server manager."}
        # End of checking allowed coins

        notifyList = await store.sql_get_tipnotify()
        if coin_family == "ERC-20" or coin_family == "TRC-20":
            real_amount = float(amount)
            token_info = await store.get_token_info(COIN_NAME)
            MinTx = token_info['real_min_tip']
            MaxTx = token_info['real_max_tip']
        else:
            real_amount = int(Decimal(amount) * get_decimal(COIN_NAME)) if coin_family in ["BCN", "XMR", "TRTL", "NANO", "XCH"] else float(amount)
            MinTx = get_min_mv_amount(COIN_NAME)
            MaxTx = get_max_mv_amount(COIN_NAME)

        # [x.guild for x in [g.members for g in self.bot.guilds] if x.id = useridyourelookingfor]
        if option == "ONLINE":
            listMembers = [member for member in ctx.guild.members if member.status != discord.Status.offline and member.bot == False]
        elif option == "ALL":
            listMembers = [member for member in ctx.guild.members if member.bot == False]

        # Check number of receivers.
        if len(listMembers) > config.tipallMax_Offchain:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} The number of receivers are too many. This command isn\'t available here."}
        # End of checking receivers numbers.
        await logchanbot("{}#{} issuing TIPALL {}{} in {}/{} with {} users.".format(ctx.author.name, ctx.author.discriminator, 
                                                                                    num_format_coin(real_amount, COIN_NAME), COIN_NAME,
                                                                                    ctx.guild.id, ctx.guild.name, len(listMembers)))
        list_receivers = []
        addresses = []
        for member in listMembers:
            # print(member.name) # you'll just print out Member objects your way.
            if ctx.author.id != member.id and member.id != self.bot.user.id:
                user_to = await store.sql_get_userwallet(str(member.id), COIN_NAME)
                if user_to is None:
                    if coin_family == "ERC-20":
                        w = await create_address_eth()
                        userregister = await store.sql_register_user(str(member.id), COIN_NAME, SERVER_BOT, 0, w)
                    elif coin_family == "TRC-20":
                        result = await store.create_address_trx()
                        userregister = await store.sql_register_user(str(member.id), COIN_NAME, SERVER_BOT, 0, result)
                    else:
                        userregister = await store.sql_register_user(str(member.id), COIN_NAME, SERVER_BOT, 0)
                    user_to = await store.sql_get_userwallet(str(member.id), COIN_NAME)
                list_receivers.append(str(member.id))

        if len(list_receivers) == 0:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} There's not users to tip to."}

        user_from = await store.sql_get_userwallet(str(ctx.author.id), COIN_NAME)
        if user_from is None:
            if coin_family == "ERC-20":
                w = await create_address_eth()
                user_from = await store.sql_register_user(str(ctx.author.id), COIN_NAME, SERVER_BOT, 0, w)
            elif coin_family == "TRC-20":
                result = await store.create_address_trx()
                user_from = await store.sql_register_user(str(ctx.author.id), COIN_NAME, SERVER_BOT, 0, result)
            else:
                user_from = await store.sql_register_user(str(ctx.author.id), COIN_NAME, SERVER_BOT, 0)

        balance_user = await get_balance_coin_user(str(ctx.author.id), COIN_NAME, discord_guild=False, server__bot=SERVER_BOT)
        if real_amount > MaxTx:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be bigger than {num_format_coin(MaxTx, COIN_NAME)} {COIN_NAME}."}
        elif real_amount < MinTx:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than {num_format_coin(MinTx, COIN_NAME)} {COIN_NAME}."}
        elif real_amount > balance_user['actual_balance']:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Insufficient balance to spread tip of {num_format_coin(real_amount, COIN_NAME)} {COIN_NAME}."}
        elif (real_amount / len(list_receivers)) < MinTx:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than {num_format_coin(MinTx, COIN_NAME)} {COIN_NAME} for each member. You need at least {num_format_coin(len(list_receivers) * MinTx, COIN_NAME)} {COIN_NAME}."}

        amountDiv = int(round(real_amount / len(list_receivers), 2))  # cut 2 decimal only
        if coin_family == "DOGE" or coin_family == "ERC-20" or coin_family == "TRC-20":
            amountDiv = round(real_amount / len(list_receivers), 4)
            if real_amount / len(list_receivers) < MinTx:
                return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} Transactions cannot be smaller than {num_format_coin(MinTx, COIN_NAME)} {COIN_NAME} for each member. You need at least {num_format_coin(len(list_receivers) * MinTx, COIN_NAME)} {COIN_NAME}."}

        if len(list_receivers) < 1:
            return {"error": f"{EMOJI_RED_NO} {ctx.author.mention} There is no one to tip to."}

        # add queue also tipall
        if ctx.author.id not in TX_IN_PROCESS:
            TX_IN_PROCESS.append(ctx.author.id)
        else:
            return {"error": f"{EMOJI_ERROR} {ctx.author.mention} You have another tx in progress."}

        tip = None
        try:
            if coin_family in ["TRTL", "BCN"]:
                tip = await store.sql_mv_cn_multiple(str(ctx.author.id), amountDiv, list_receivers, 'TIPALL', COIN_NAME)
            elif coin_family == "XMR":
                tip = await store.sql_mv_xmr_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL")
            elif coin_family == "XCH":
                tip = await store.sql_mv_xch_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL")
            elif coin_family == "NANO":
                tip = await store.sql_mv_nano_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL")
            elif coin_family == "DOGE":
                tip = await store.sql_mv_doge_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL")
            elif coin_family == "ERC-20":
                tip = await store.sql_mv_erc_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL", token_info['contract'])
            elif coin_family == "TRC-20":
                tip = await store.sql_mv_trx_multiple(str(ctx.author.id), list_receivers, amountDiv, COIN_NAME, "TIPALL", token_info['contract'])
        except Exception as e:
            await logchanbot(traceback.format_exc())
        await asyncio.sleep(config.interval.tx_lap_each)

        # remove queue from tipall
        if ctx.author.id in TX_IN_PROCESS:
            TX_IN_PROCESS.remove(ctx.author.id)

        if tip:
            # Update tipstat
            try:
                update_tipstat = await store.sql_user_get_tipstat(str(ctx.author.id), COIN_NAME, True, SERVER_BOT)
            except Exception as e:
                await logchanbot(traceback.format_exc())
            tipAmount = num_format_coin(real_amount, COIN_NAME)
            ActualSpend_str = num_format_coin(amountDiv * len(list_receivers), COIN_NAME)
            amountDiv_str = num_format_coin(amountDiv, COIN_NAME)
            numMsg = 0
            total_found = 0
            max_mention = 40
            numb_mention = 0
            tmp_message = await ctx.reply("Sending message...")
            if len(listMembers) < max_mention:
                # DM all user
                for member in listMembers:
                    if ctx.author.id != member.id and member.id != self.bot.user.id:
                        total_found += 1
                        if str(member.id) not in notifyList:
                            # random user to DM
                            # dm_user = bool(random.getrandbits(1)) if len(listMembers) > config.tipallMax_LimitDM else True
                            try:
                                await member.send(
                                    f'{EMOJI_MONEYFACE} You got a tip of {amountDiv_str} '
                                    f'{COIN_NAME} from {ctx.author.name}#{ctx.author.discriminator} `.tipall` in server `{ctx.guild.name}` #{ctx.channel.name}\n'
                                    f'{NOTIFICATION_OFF_CMD}')
                                numMsg += 1
                            except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                                await store.sql_toggle_tipnotify(str(member.id), "OFF")
            else:
                # mention all user
                send_tipped_ping = 0
                list_user_mention = []
                list_user_mention_str = ""
                list_user_not_mention = []
                list_user_not_mention_str = ""
                random.shuffle(listMembers)
                for member in listMembers:
                    if send_tipped_ping >= config.maxTipMessage:
                        total_found += 1
                    else:
                        if ctx.author.id != member.id and member.id != self.bot.user.id:
                            if str(member.id) not in notifyList:
                                list_user_mention.append("{}".format(member.mention))
                            else:
                                list_user_not_mention.append("{}#{}".format(member.name, member.discriminator))
                        total_found += 1
                        numb_mention += 1

                        # Check if a batch meets
                        if numb_mention > 0 and numb_mention % max_mention == 0:
                                # send the batch
                            if len(list_user_mention) >= 1:
                                list_user_mention_str = ", ".join(list_user_mention)
                            if len(list_user_not_mention) >= 1:
                                list_user_not_mention_str = ", ".join(list_user_not_mention)
                            try:
                                if len(list_user_mention_str) > 5 or len(list_user_not_mention_str) > 5:
                                    await ctx.reply(
                                        f'{EMOJI_MONEYFACE} {list_user_mention_str} {list_user_not_mention_str}, You got a tip of {amountDiv_str} {COIN_NAME} '
                                        f'from {ctx.author.name}#{ctx.author.discriminator}'
                                        f'{NOTIFICATION_OFF_CMD}')
                                    send_tipped_ping += 1
                            except Exception as e:
                                pass
                            # reset
                            list_user_mention = []
                            list_user_mention_str = ""
                            list_user_not_mention = []
                            list_user_not_mention_str = ""
                # if there is still here
                if len(list_user_mention) + len(list_user_not_mention) > 1:
                    if len(list_user_mention) >= 1:
                        list_user_mention_str = ", ".join(list_user_mention)
                    if len(list_user_not_mention) >= 1:
                        list_user_not_mention_str = ", ".join(list_user_not_mention)
                    try:
                        remaining_str = ""
                        if numb_mention < total_found:
                            remaining_str = " and other {} members".format(total_found-numb_mention)
                        await ctx.reply(
                                f'{EMOJI_MONEYFACE} {list_user_mention_str} {list_user_not_mention_str} {remaining_str}, You got a tip of {amountDiv_str} '
                                f'{COIN_NAME} from {ctx.author.name}#{ctx.author.discriminator}'
                                f'{NOTIFICATION_OFF_CMD}')
                    except Exception as e:
                        try:
                            await ctx.reply(f'**({total_found})** members got {amountDiv_str} {COIN_NAME} :) Too many to mention :) Phew!!!')
                        except Exception as e:
                            await ctx.message.add_reaction(EMOJI_ZIPPED_MOUTH)
            # tipper shall always get DM. Ignore notifyList
            await tmp_message.delete()
            try:
                await ctx.author.send(
                    f'{EMOJI_ARROW_RIGHTHOOK} Tip of {tipAmount} '
                    f'{COIN_NAME} '
                    f'was sent spread to ({total_found}) members in server `{ctx.guild.name}`.\n'
                    f'Each member got: `{amountDiv_str} {COIN_NAME}`\n'
                    f'Actual spending: `{ActualSpend_str} {COIN_NAME}`')
            except (discord.Forbidden, discord.errors.Forbidden, discord.errors.HTTPException) as e:
                await store.sql_toggle_tipnotify(str(ctx.author.id), "OFF")
            return


    @dislash.guild_only()
    @inter_client.slash_command(usage="tipall <amount> <coin> [online|all]",
                                options=[
                                    Option('amount', 'amount', OptionType.STRING, required=True),
                                    Option('coin', 'coin', OptionType.STRING, required=True),
                                    Option('option', 'online | all', OptionType.STRING, required=False)
                                ],
                                description="Tip all users in the guild.")
    async def tipall(
        self, 
        ctx, 
        amount: str, 
        coin: str, 
        option: str=None
    ):
        amount = amount.replace(",", "")
        try:
            amount = Decimal(amount)
        except ValueError:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.reply(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
            return
        tip_all = await self.tip_all(ctx, amount, coin.upper(), option)
        if tip_all and "error" in tip_all:
            await ctx.reply(tip_all['error'], ephemeral=True)


    @commands.guild_only()
    @commands.command(
        usage="tipall <amount> <coin> [option]", 
        description="Tip all users in the guild."
    )
    async def tipall(
        self, 
        ctx, 
        amount: str, 
        coin: str, 
        option: str=None
    ):
        amount = amount.replace(",", "")
        try:
            amount = Decimal(amount)
        except ValueError:
            await ctx.message.add_reaction(EMOJI_ERROR)
            await ctx.reply(f'{EMOJI_RED_NO} {ctx.author.mention} Invalid amount.')
            return
        if option is None: option = "ONLINE"
        tip_all = await self.tip_all(ctx, amount, coin.upper(), option)
        if tip_all and "error" in tip_all:
            msg = await ctx.reply(tip_all['error'], components=[row_close_message])
            await store.add_discord_bot_message(str(msg.id), "DM" if isinstance(ctx.channel, discord.DMChannel) else str(ctx.guild.id), str(ctx.author.id))


def setup(bot):
    bot.add_cog(TipTipAll(bot))