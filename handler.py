import json
import logging
import os
import time
from decimal import *
from dydx3 import Client
from dydx3 import private_key_to_public_key_pair_hex
from dydx3.constants import *

import boto3

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

QUEUE_URL = os.getenv('QUEUE_URL')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
SECRET = os.getenv('SECRET')
KEY = os.getenv('KEY')
PASSPHRASE = os.getenv('PASSPHRASE')
LEGACY_SIGNING = os.getenv('LEGACY_SIGNING') == 'True'
WALLET_TYPE = os.getenv('WALLET_TYPE')
STARK_PRIVATE_KEY = os.getenv('STARK_PRIVATE_KEY')
MAINNET = os.getenv('MAINNET') == 'True'

NETWORK_ID = NETWORK_ID_ROPSTEN
API_HOST = API_HOST_ROPSTEN
if MAINNET:
    NETWORK_ID = NETWORK_ID_MAINNET
    API_HOST = API_HOST_MAINNET


def trade(event, context):
    if not event.get('Records')[0].get('Sns').get('Message'):
        return {'statusCode': 400, 'body': json.dumps({'message': 'No `Message` was found'})}
    message = json.loads(event['Records'][0]['Sns']['Message'])

    api_key_credentials = {
        "walletAddress": WALLET_ADDRESS,
        "secret": SECRET,
        "key": KEY,
        "passphrase": PASSPHRASE,
        "legacySigning": LEGACY_SIGNING,
        "walletType": WALLET_TYPE
    }
    # Set STARK key.
    stark_private_key = STARK_PRIVATE_KEY
    public_x, public_y = private_key_to_public_key_pair_hex(stark_private_key)

    client = Client(
        network_id=NETWORK_ID,
        host=API_HOST,
        default_ethereum_address=WALLET_ADDRESS,
        stark_private_key=stark_private_key,
        stark_public_key=public_x,
        stark_public_key_y_coordinate=public_y,
        api_key_credentials=api_key_credentials,
        web3=None,
    )

    account_response = client.private.get_account(WALLET_ADDRESS).data
    print('account_response', account_response)
    logger.info('account_response', account_response)
    position_id = account_response['account']['positionId']
    equity = Decimal(account_response['account']['equity'])
    user = client.private.get_user().data['user']
    makerFeeRate = user['makerFeeRate']
    takerFeeRate = user['takerFeeRate']

    marketData = client.public.get_markets(
        market=MARKET_ETH_USD).data['markets'][MARKET_ETH_USD]
    logger.debug('marketData data {}'.format(marketData))
    tickSize = marketData['tickSize']
    stepSize = marketData['stepSize']
    oraclePrice = Decimal(marketData['oraclePrice'])

    print('event', message, 'tickSize', tickSize)
    orderSize = Decimal(message.get('size')).quantize(Decimal(stepSize))
    price = Decimal(message.get('price')).quantize(Decimal(tickSize))
    maxTxFee = Decimal(message.get('maxTxFee')).quantize(Decimal(stepSize))
    estimatedFeePercent = Decimal(
        max(makerFeeRate, takerFeeRate)).quantize(Decimal(stepSize))
    estimatedFee = Decimal(estimatedFeePercent * orderSize * price)

    eth_position = client.private.get_positions(
        market=MARKET_ETH_USD,
        status=POSITION_STATUS_OPEN,
    ).data['positions'][0]
    positionSize = Decimal(eth_position['size'])

    leverage = ((abs(positionSize) + abs(orderSize)) * oraclePrice)/equity
    if leverage > Decimal(1.0):
        error = {
            'message': 'Not enough equity to stay out of margin, estimated leverage: {}'.format(leverage)}
        logger.exception(json.dumps(error))
        return {'statusCode': 400, 'body': json.dumps(error)}

    if estimatedFee > maxTxFee:
        error = json.dumps(
            {'message': 'Max Tx Fee exceeded, {} > {}'.format(estimatedFee, maxTxFee)})
        logger.exception(error)
        return {'statusCode': 400, 'body': error}

    order_params = {
        'position_id': position_id,
        'market': MARKET_ETH_USD,
        'side': ORDER_SIDE_BUY,
        'order_type': ORDER_TYPE_LIMIT,
        'post_only': False,
        'size': str(orderSize),
        'price': str(price),
        'limit_fee': str(estimatedFeePercent),
        'expiration_epoch_seconds': time.time() + 120,
    }
    order_response = client.private.create_order(**order_params).data
    order_id = order_response['order']['id']
    logger.info("Order created: {}".format(order_response))
    return {'statusCode': 200, 'body': json.dumps({'order_response': order_response})}


def producer(event, context):
    status_code = 200
    message = ''

    if not event.message:
        return {'statusCode': 400, 'body': json.dumps({'message': 'No body was found'})}

    try:
        message_attrs = {
            'AttributeName': {'StringValue': 'AttributeValue', 'DataType': 'String'}
        }

    except Exception as e:
        logger.exception('Sending message to SQS queue failed!')
        message = str(e)
        status_code = 500

    return {'statusCode': status_code, 'body': json.dumps({'message': message})}


def cost_basis_sell(event, context):

    api_key_credentials = {
        "walletAddress": WALLET_ADDRESS,
        "secret": SECRET,
        "key": KEY,
        "passphrase": PASSPHRASE,
        "legacySigning": LEGACY_SIGNING,
        "walletType": WALLET_TYPE
    }
    # Set STARK key.
    stark_private_key = STARK_PRIVATE_KEY
    public_x, public_y = private_key_to_public_key_pair_hex(stark_private_key)

    client = Client(
        network_id=NETWORK_ID,
        host=API_HOST,
        default_ethereum_address=WALLET_ADDRESS,
        stark_private_key=stark_private_key,
        stark_public_key=public_x,
        stark_public_key_y_coordinate=public_y,
        api_key_credentials=api_key_credentials,
        web3=None,
    )

    marketData = client.public.get_markets(
        MARKET_ETH_USD).data['markets'][MARKET_ETH_USD]
    tickSize = Decimal(marketData['tickSize'])
    stepSize = Decimal(marketData['stepSize'])

    position_response = client.private.get_positions(
        market=MARKET_ETH_USD,
        status=POSITION_STATUS_OPEN,
    ).data['positions'][0]
    logger.debug(position_response)
    position_size = Decimal(position_response['size'])
    entry_price = Decimal(
        position_response['entryPrice'])
    cost_basis = (entry_price).quantize(Decimal(stepSize))
    print('cost_basis', cost_basis)
    logger.info('cost_basis' + str(cost_basis))

    indexPrice = Decimal(marketData['indexPrice']).quantize(tickSize)

    account_response = client.private.get_account(WALLET_ADDRESS).data
    print('account_response', account_response)
    logger.info('account_response', account_response)
    position_id = account_response['account']['positionId']
    user = client.private.get_user().data['user']
    makerFeeRate = Decimal(user['makerFeeRate'])
    takerFeeRate = Decimal(user['takerFeeRate'])
    estimatedFeePercent = Decimal(
        max(makerFeeRate, takerFeeRate)).quantize(Decimal(stepSize))
    # Constants
    SELL_SIZE = Decimal(0.01).quantize(Decimal(stepSize))  # ETH
    PROFIT_PERCENT = Decimal(1.01).quantize(Decimal('1.00'))

    estimatedFee = Decimal(estimatedFeePercent * SELL_SIZE)
    if indexPrice < cost_basis * PROFIT_PERCENT + estimatedFee:
        error = {'message': 'indexPrice {} is not {} times greater than cost basis of {} + fee of {}'.format(
            indexPrice, PROFIT_PERCENT, cost_basis, estimatedFee)}
        logger.exception(json.dumps(error))
        return {'statusCode': 400, 'body': json.dumps(error)}

    if SELL_SIZE + estimatedFeePercent * SELL_SIZE >= position_size:
        error = {'message': 'Position size exceeded, {} > {}'.format(
            SELL_SIZE + estimatedFeePercent * SELL_SIZE, position_size)}
        logger.exception(json.dumps(error))
        return {'statusCode': 400, 'body': json.dumps(error)}

        # Sanity checks passed, lets sell!
    order_params = {
        'position_id': position_id,
        'market': MARKET_ETH_USD,
        'side': ORDER_SIDE_SELL,
        'order_type': ORDER_TYPE_LIMIT,
        'post_only': False,
        'size': str(SELL_SIZE),
        'price': str(indexPrice),
        'limit_fee': str(estimatedFeePercent),
        'expiration_epoch_seconds': time.time() + 120,
    }
    order_response = client.private.create_order(**order_params).data
    order_id = order_response['order']['id']
    logger.info("Order created: {}".format(order_response))
    return {'statusCode': 200, 'body': json.dumps({'order_response': order_response})}
