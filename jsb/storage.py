import os

import yaml

class Store(object):
    def __init__(self, backend=None):
        if not backend:
            backend = InMemoryBackend()

        self.backend = backend
        self.data = backend.load()

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        if self.data.get(key) == value:
            return False

        self.data[key] = value
        self.save_to_backend()
        return True

    def hget(self, key, field):
        if key not in self.data:
            return

        return self.data[key].get(field)

    def hset(self, key, field, value):
        if key not in self.data:
            self.data[key] = {}

        if self.data[key].get(field) == value:
            return False

        self.data[key][field] = value
        self.backend.save(self.data)
        return True

    def sismember(self, key, value):
        if key not in self.data:
            return False

        return value in self.data[key]

    def sadd(self, key, value):
        if key not in self.data:
            self.data[key] = set()

        s = self.data[key]
        if value in s:
            return False

        s.add(value)
        self.save_to_backend()
        return True

    def sismember(self, key, value):
        if key not in self.data:
            return False

        return value in self.data[key]

    def srem(self, key, value):
        if key not in data:
            return False

        s = self.data[key]
        if value not in s:
            return False

        s.remove(value)
        self.save_to_backend()
        return True

    def save_to_backend(self):
        self.backend.save(self.data)

class Backend(object):
    def load(self):
        pass

    def save(self, data):
        pass

class FileBackend(Backend):
    def __init__(self, path):
        self.path = path

    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as fp:
                return yaml.load(fp)

        return {}

    def save(self, data):
        with open(self.path, 'w') as fp:
            yaml.dump(data, fp, default_flow_style=False)

class InMemoryBackend(Backend):
    def __init__(self):
        self.data = {}

    def load(self):
        return self.data

    def save(self, data):
        self.data = data
