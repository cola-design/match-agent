import uuid

class Graph:

    def __init__(self):
        self.nodes = []
        self.edges = []
        self.counter = 2020000000000

    def node(self, kind, title, **data):
        self.counter += 1
        identity = str(self.counter)
        index = len(self.nodes)
        d = {'type': kind, 'title': title, 'desc': '', 'selected': False}
        d.update(data)
        starter = kind == 'iteration-start'
        n = {'id': identity, 'type': 'custom-iteration-start' if starter else 'custom', 'data': d, 'position': {'x': index % 7 * 310, 'y': index // 7 * 210}, 'positionAbsolute': {'x': index % 7 * 310, 'y': index // 7 * 210}, 'width': 44 if starter else d.get('width', 260), 'height': 48 if starter else d.get('height', 120), 'sourcePosition': 'right', 'targetPosition': 'left', 'selected': False}
        if starter:
            n.update(selectable=False, draggable=False)
        self.nodes.append(n)
        return identity

    def data(self, n):
        return next((x['data'] for x in self.nodes if x['id'] == n))

    def edge(self, a, b, handle='source'):
        internal = bool(self.data(a).get('isInIteration'))
        self.edges.append({'id': a + '-' + handle + '-' + b, 'source': a, 'target': b, 'sourceHandle': handle, 'targetHandle': 'target', 'type': 'custom', 'data': {'sourceType': self.data(a)['type'], 'targetType': self.data(b)['type'], 'isInIteration': internal, 'isInLoop': False, **({'iteration_id': self.data(a)['iteration_id']} if internal else {})}, 'zIndex': 1002 if internal else 0})

    def answer(self, title, text):
        return self.node('answer', title, answer=text, variables=[])

    def branch(self, title, selector, values):
        return self.node('if-else', title, cases=[{'id': value, 'case_id': value, 'logical_operator': 'and', 'conditions': [{'id': str(uuid.uuid4()), 'comparison_operator': 'is', 'variable_selector': selector, 'value': value, 'varType': 'string'}]} for value in values])

    def inside(self, node, parent, x):
        n = next((v for v in self.nodes if v['id'] == node))
        n.update(parentId=parent, zIndex=1002)
        n['position'] = {'x': x, 'y': 70}
        n['positionAbsolute'] = {'x': x, 'y': 70}
        n['data'].update(isInIteration=True, iteration_id=parent)

    def layout(self):
        roots = {n['id']: n for n in self.nodes if 'parentId' not in n}
        parents = {k: set() for k in roots}
        children = {k: [] for k in roots}
        for edge in self.edges:
            a, b = (edge['source'], edge['target'])
            if a in roots and b in roots:
                parents[b].add(a)
                children[a].append(b)
        pending = {k: set(v) for k, v in parents.items()}
        ready = [k for k in roots if not pending[k]]
        rank = {k: 0 for k in ready}
        seen = set()
        while ready:
            key = ready.pop(0)
            if key in seen:
                continue
            seen.add(key)
            for child in children[key]:
                rank[child] = max(rank.get(child, 0), rank[key] + 1)
                pending[child].discard(key)
                if not pending[child]:
                    ready.append(child)
        x = 0
        for depth in range(max(rank.values()) + 1):
            group = [n for k, n in roots.items() if rank[k] == depth]
            group.sort(key=lambda n: n['data']['type'] == 'answer')
            for i, node in enumerate(group):
                node['position'] = dict(x=x, y=i * 650)
                node['positionAbsolute'] = dict(node['position'])
            x += max([n.get('width', 260) for n in group] + [260]) + 90
        for node in self.nodes:
            if 'parentId' in node:
                parent = roots[node['parentId']]['position']
                pos = node['position']
                node['positionAbsolute'] = {'x': parent['x'] + pos['x'], 'y': parent['y'] + pos['y']}
        by_id = {n['id']: n for n in self.nodes}
        for edge in self.edges:
            parent = by_id[edge['source']].get('parentId')
            internal = bool(parent and parent == by_id[edge['target']].get('parentId'))
            edge['data']['isInIteration'] = internal
            edge['zIndex'] = 1002 if internal else 0
            if internal:
                edge['data']['iteration_id'] = parent
            else:
                edge['data'].pop('iteration_id', None)
