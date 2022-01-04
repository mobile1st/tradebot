import json
import logging
import os
import time
from decimal import *
from dydx3 import Client
from dydx3 import private_key_to_public_key_pair_hex
from dydx3.constants import *
from web3 import Web3

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


def trade(event, context):
    if not event.get('body'):
        return {'statusCode': 400, 'body': json.dumps({'message': 'No body was found'})}

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
        network_id=NETWORK_ID_ROPSTEN,
        host=API_HOST_ROPSTEN,
        default_ethereum_address=WALLET_ADDRESS,
        stark_private_key=stark_private_key,
        stark_public_key=public_x,
        stark_public_key_y_coordinate=public_y,
        api_key_credentials=api_key_credentials,
        web3=None,
    )

    # # Onboard the account.
    # onboarding_response = client.onboarding.create_user(
    #     stark_public_key=public_x,
    #     stark_public_key_y_coordinate=public_y,
    # )
    # print('onboarding_response', onboarding_response)

    # Query a private endpoint.
    accounts_response = client.private.get_accounts()
    print('accounts_response', accounts_response)
    logger.info('accounts_response', accounts_response)

    account_response = client.private.get_account().data
    position_id = account_response['account']['positionId']
    free_collateral = account_response['account']['freeCollateral']
    user = client.private.get_user().data
    makerFeeRate = user['makerFeeRate']
    takerFeeRate = user['takerFeeRate']

    size = Decimal(event.get('body').get('size'))
    price = Decimal(event.get('body').get('price'))
    maxTxFee = Decimal(event.get('body').get('maxTxFee'))

    if max(makerFeeRate, takerFeeRate) * size * price > maxTxFee:
        return {'statusCode': 400, 'body': json.dumps({'message': 'Max Tx Fee exceeded, {} > {}'.format(max(makerFeeRate, takerFeeRate) * size * price, maxTxFee)})}
    if size * price + max(makerFeeRate, takerFeeRate) * size * price > free_collateral:
        return {'statusCode': 400, 'body': json.dumps({'message': 'Free Collateral exceeded, {} > {}'.format(size * price + max(makerFeeRate, takerFeeRate) * size * price, free_collateral)})}

    order_params = {
        'position_id': position_id,
        'market': MARKET_ETH_USD,
        'side': ORDER_SIDE_BUY,
        'order_type': ORDER_TYPE_LIMIT,
        'post_only': False,
        'size': size,
        'price': price,
        'limit_fee': max(makerFeeRate, takerFeeRate),
        'expiration_epoch_seconds': time.time() + 120,
    }
    order_response = client.private.create_order(**order_params).data
    order_id = order_response['order']['id']
    logger.info("Order created: {}".format(order_response))
    return {'statusCode': 200, 'body': json.dumps({'order_response': order_response})}


def producer(event, context):
    status_code = 200
    message = ''

    if not event.get('body'):
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


def consumer(event, context):
    for record in event['Records']:
        logger.info(f'Message body: {record["body"]}')
        logger.info(
            f'Message attribute: {record["messageAttributes"]["AttributeName"]["stringValue"]}'
        )
