import unittest

from jsb import storage

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.backend = storage.InMemoryBackend()
        self.store = storage.Store(self.backend)

    def test_operations(self):
        assert not self.store.sismember('test', 1)
        self.store.sadd('test', 1)
        assert self.store.sismember('test', 1)

        expected = {
            'test': set([1])
        }

        assert self.backend.data == expected
