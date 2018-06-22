from logging import getLogger

from django.conf import settings
from ethereum.utils import (check_checksum, checksum_encode, ecrecover_to_pub,
                            privtoaddr, sha3)
from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware
from web3.utils.threads import Timeout

from safe_relay_service.gas_station.gas_station import GasStation

logger = getLogger(__name__)


class EthereumService:
    NULL_ADDRESS = '0x' + '0' * 40

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self):
        self.w3 = Web3(HTTPProvider(settings.ETHEREUM_NODE_URL))
        try:
            if self.w3.net.chainId != 1:
                self.w3.middleware_stack.inject(geth_poa_middleware, layer=0)
            # For tests using dummy connections (like IPC)
        except (ConnectionError, FileNotFoundError):
            self.w3.middleware_stack.inject(geth_poa_middleware, layer=0)

        self.gas_station = GasStation(settings.ETHEREUM_NODE_URL, settings.GAS_STATION_NUMBER_BLOCKS)

    def get_nonce_for_account(self, address):
        return self.w3.eth.getTransactionCount(address, 'pending')

    @property
    def current_block_number(self):
        return self.w3.eth.blockNumber

    def get_balance(self, address, block_identifier=None):
        return self.w3.eth.getBalance(address, block_identifier)

    def get_transaction_receipt(self, tx_hash, timeout=None):
        if not timeout:
            return self.w3.eth.getTransactionReceipt(tx_hash)
        else:
            try:
                return self.w3.eth.waitForTransactionReceipt(tx_hash, timeout=timeout)
            except Timeout:
                return None

    def send_raw_transaction(self, raw_transaction):
        return self.w3.eth.sendRawTransaction(bytes(raw_transaction))

    def send_eth_to(self, to: str, gas_price: int, value: int, gas: int=22000) -> bytes:
        """
        Send ether using configured account
        :param to: to
        :param gas_price: gas_price
        :param value: value(wei)
        :param gas: gas, defaults to 22000
        :return: tx_hash
        """

        assert check_checksum(to)

        assert value < self.w3.toWei(settings.SAFE_FUNDER_MAX_ETH, 'ether')

        private_key = settings.SAFE_FUNDER_PRIVATE_KEY

        if private_key:
            ethereum_account = self.private_key_to_address(private_key)
            tx = {
                    'to': to,
                    'value': value,
                    'gas': gas,
                    'gasPrice': gas_price,
                    'nonce': self.get_nonce_for_account(ethereum_account),
                }

            signed_tx = self.w3.eth.account.signTransaction(tx, private_key=private_key)
            logger.debug('Sending %d wei from %s to %s', value, ethereum_account, to)
            return self.w3.eth.sendRawTransaction(signed_tx.rawTransaction)
        elif self.w3.eth.accounts:
            ethereum_account = self.w3.eth.accounts[0]
            tx = {
                    'from': ethereum_account,
                    'to': to,
                    'value': value,
                    'gas': gas,
                    'gasPrice': gas_price,
                    'nonce': self.get_nonce_for_account(ethereum_account),
                }
            logger.debug('Sending %d wei from %s to %s', value, ethereum_account, to)
            return self.w3.eth.sendTransaction(tx)
        else:
            logger.error('No ethereum account configured')
            raise ValueError("Ethereum account was not configured or unlocked in the node")

    def check_tx_with_confirmations(self, tx_hash: str, confirmations: int) -> bool:
        """
        Check tx hash and make sure it has the confirmations required
        :param w3: Web3 instance
        :param tx_hash: Hash of the tx
        :param confirmations: Minimum number of confirmations required
        :return: True if tx was mined with the number of confirmations required, False otherwise
        """
        tx_receipt = self.w3.eth.getTransactionReceipt(tx_hash)
        if not tx_receipt:
            return False
        else:
            block_number = self.w3.eth.blockNumber
            tx_block_number = tx_receipt['blockNumber']
            return (block_number - tx_block_number) >= confirmations

    @staticmethod
    def private_key_to_address(private_key):
        return checksum_encode(privtoaddr(private_key))

    @staticmethod
    def get_signing_address(hash, v, r, s) -> str:
        """
        :return: checksum encoded address starting by 0x, for example `0x568c93675A8dEb121700A6FAdDdfE7DFAb66Ae4A`
        :rtype: str
        """
        encoded_64_address = ecrecover_to_pub(hash, v, r, s)
        address_bytes = sha3(encoded_64_address)[-20:]
        return checksum_encode(address_bytes)