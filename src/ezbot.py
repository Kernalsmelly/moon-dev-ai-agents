'''
🌙 Moon Dev Trading Bot Control Panel 
Controls different trading actions that our AI agents can execute:

0 - close a position (in chunks)
1 - open a position (in chunks)
2 - stop loss: close under X price
3 - break out: buy over Y price (in chunks)
4 - data bot: get OHLCV data for a solana contract address
5 - market maker - buy under X price and sell over Y price

not done yet
# 4 - pnl close, just monitor position for tp and sl
# 6 - funding buy
# 7 - liquidation amount
'''

from src import nice_funcs as n
import time
from termcolor import cprint
import schedule
import src.config as config


###### ASKING USER WHAT THEY WANNA DO - WILL REMOVE USER SOON AND REPLACE WITH BOT ######
action = 0
print('🌙 Moon Dev says: slow down, dont trade by hand... take it easy! 🚀')
action = input('0 to close, 1 to buy, 2 stop loss, 3 breakout, 5 market maker  |||| 6 funding buy, 7 liquidation amount:')
print('you entered:', action)
action = int(action)

def bot():
    """Single-run control loop called by scheduler. Uses config values and nice_funcs (n)."""
    try:
        if action == 0:
            cprint('closing position', 'cyan')
            # get pos first
            pos = n.get_position(config.symbol)
            while pos > 0:
                n.chunk_kill(config.symbol, config.max_usd_order_size, config.slippage)
                pos = n.get_position(config.symbol)
                time.sleep(1)

            if pos < 0.9:
                time.sleep(15)
                pos = n.get_position(config.symbol)
                if pos < 0.9:
                    print('position closed thanks moon dev....')
                    time.sleep(config.SLEEP_AFTER_CLOSE)

        elif action == 1:
            cprint('opening buying position', 'cyan')
            pos = n.get_position(config.symbol)
            price = n.token_price(config.symbol)
            pos_usd = pos * price

            while pos_usd < (0.97 * config.usd_size):
                print(f'position: {round(pos,2)} price: {round(price,8)} buy_under: {config.buy_under} pos_usd: ${round(pos_usd,2)}')
                try:
                    size_needed = config.usd_size - pos_usd
                    if size_needed > config.max_usd_order_size:
                        chunk_size = config.max_usd_order_size
                    else:
                        chunk_size = size_needed

                    chunk_size = int(chunk_size * 10**6)
                    chunk_size = str(chunk_size)

                    for _ in range(config.orders_per_open):
                        n.market_buy(config.symbol, chunk_size, config.slippage)
                        cprint(f'chunk buy submitted of {config.symbol[-4:]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                        time.sleep(1)

                    time.sleep(config.tx_sleep)

                    pos = n.get_position(config.symbol)
                    price = n.token_price(config.symbol)
                    pos_usd = pos * price

                except Exception as e:
                    print(f'❌ ERROR IN ezbot.py during buy: {e}')
                    cprint('trying again to make the order in 30 seconds.....', 'light_blue', 'on_light_magenta')
                    time.sleep(30)
                    # one retry
                    try:
                        for _ in range(config.orders_per_open):
                            n.market_buy(config.symbol, chunk_size, config.slippage)
                            cprint(f'chunk buy submitted of {config.symbol[-4:]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                            time.sleep(1)

                        time.sleep(config.tx_sleep)
                        pos = n.get_position(config.symbol)
                        price = n.token_price(config.symbol)
                        pos_usd = pos * price
                    except Exception as e:
                        print(f'❌ FINAL ERROR IN ezbot.py during buy retry: {e}')
                        cprint('Final Error in the buy, restart needed', 'white', 'on_red')
                        time.sleep(10)
                        break

            cprint(f'position filled of {config.symbol[-4:]} total: ${pos_usd}', 'white', 'on_green')

        elif action == 2:
            pos = n.get_position(config.symbol)
            price = n.token_price(config.symbol)
            pos = float(pos)
            print(f'stop loss: close if price under {config.STOPLOSS_PRICE} current price is {price}')
            if price < config.STOPLOSS_PRICE and pos > 0:
                print(f'selling {config.symbol[-4:]} bc price is {price}  is under {config.STOPLOSS_PRICE}')
                n.chunk_kill(config.symbol, config.max_usd_order_size, config.slippage)
                print('chunk kill complete... thank you moon dev you are my savior 777')
                time.sleep(15)
            else:
                print(f'price is {price} and pos is {pos}')
                time.sleep(30)

        elif action == 3:
            pos = n.get_position(config.symbol)
            price = n.token_price(config.symbol)
            pos_usd = pos * price
            size_needed = config.usd_size - pos_usd
            if size_needed > config.max_usd_order_size:
                chunk_size = config.max_usd_order_size
            else:
                chunk_size = size_needed

            print(f'breakout action called, buying over {config.BREAKOUT_PRICE} current price is {price} & pos is ${pos_usd}')
            chunk_size = int(chunk_size * 10**6)
            chunk_size = str(chunk_size)
            print(f'BREAKOUT_PRICE: {config.BREAKOUT_PRICE} pos_usd: {pos_usd} usd_size: {config.usd_size} price: {price}')
            if (price > config.BREAKOUT_PRICE) and (pos_usd < config.usd_size):
                time.sleep(1)
                pos = n.get_position(config.symbol)
                price = n.token_price(config.symbol)
                pos_usd = pos * price
                size_needed = config.usd_size - pos_usd
                if size_needed > config.max_usd_order_size:
                    chunk_size = config.max_usd_order_size
                else:
                    chunk_size = size_needed
                chunk_size = int(chunk_size * 10**6)
                chunk_size = str(chunk_size)
                if (pos_usd < config.usd_size) and (price > config.BREAKOUT_PRICE):
                    print(f'buying {config.symbol[-4:]} bc price is {price} and breakoutprice is {config.BREAKOUT_PRICE}')
                    n.breakout_entry(config.symbol, config.BREAKOUT_PRICE)
                    print('breakout entry complete, thanks moon dev...')
                    time.sleep(15)

        elif action == 5:
            print(f'market maker buying below {config.buy_under} and selling above {config.sell_over}')
            pos = n.get_position(config.symbol)
            price = n.token_price(config.symbol)
            pos_usd = pos * price
            size_needed = config.usd_size - pos_usd
            if size_needed > config.max_usd_order_size:
                chunk_size = config.max_usd_order_size
            else:
                chunk_size = size_needed
            chunk_size = int(chunk_size * 10**6)
            chunk_size = str(chunk_size)

            if price > config.sell_over:
                print(f'selling {config.symbol[-4:]} bc price is {price} and sell over is {config.sell_over}')
                n.chunk_kill(config.symbol, config.max_usd_order_size, config.slippage)
                print('chunk kill complete... thank you moon dev you are my savior 777')
                time.sleep(15)

            elif (price < config.buy_under) and (pos_usd < config.usd_size):
                time.sleep(10)
                pos = n.get_position(config.symbol)
                price = n.token_price(config.symbol)
                pos_usd = pos * price
                size_needed = config.usd_size - pos_usd
                if size_needed > config.max_usd_order_size:
                    chunk_size = config.max_usd_order_size
                else:
                    chunk_size = size_needed
                chunk_size = int(chunk_size * 10**6)
                chunk_size = str(chunk_size)
                if (pos_usd < config.usd_size) and (price < config.buy_under):
                    print(f'buying {config.symbol[-4:]} bc price is {price} and buy under is {config.buy_under}')
                    n.elegant_entry(config.symbol, config.buy_under)
                    print('elegant entry complete...')
                    time.sleep(15)

        elif action == 6:
            print('funding buy')

        elif action == 7:
            print('liquidation amount')

        else:
            print('COMPLETE THANKS MOON DEV!')

    except Exception as e:
        print(f'*** error in bot(): {e}')


bot()

schedule.every(30).seconds.do(bot)

while True:
    try:
        schedule.run_pending()
        time.sleep(3)
    except Exception as e:
        print(f'*** error, sleeping: {e}')
        time.sleep(15)
