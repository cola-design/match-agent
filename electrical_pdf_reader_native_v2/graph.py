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

    def code(self, title, function, inputs, outputs, verify=False):
        return self.node('code', title, code_language='python3', code=source(function, verify), variables=[{'variable': key, 'value_selector': value} for key, value in inputs.items()], outputs={key: {'type': value, 'children': None} for key, value in outputs.items()})

    def answer(self, title, text):
        return self.node('answer', title, answer=text, variables=[])

    def persist(self, previous):
        n = self.node('assigner', '保存项目状态到当前Dify会话', version='2', items=[{'variable_selector': ['conversation', 'project_state'], 'input_type': 'variable', 'operation': 'over-write', 'value': [previous, 'state_json']}])
        self.edge(previous, n)
        return n

    def branch(self, title, selector, values):
        return self.node('if-else', title, cases=[{'id': value, 'case_id': value, 'logical_operator': 'and', 'conditions': [{'id': str(uuid.uuid4()), 'comparison_operator': 'is', 'variable_selector': selector, 'value': value, 'varType': 'string'}]} for value in values])

    def valid(self, node):
        gate = self.branch('是否校验通过', [node, 'valid'], ['YES'])
        self.edge(node, gate)
        error = self.answer('说明待处理问题', '{{#' + node + '.message#}}')
        self.edge(gate, error, 'false')
        return gate

    def llm(self, title, system, user, vision_selector=None):
        return self.node('llm', title, context={'enabled': False, 'variable_selector': []}, model={'provider': 'langgenius/tongyi/tongyi', 'name': 'qwen-vl-max' if vision_selector else 'qwen3.7-max', 'mode': 'chat', 'completion_params': {'temperature': 0, 'max_tokens': 8192}}, prompt_template=[{'id': uuid.uuid4().hex, 'role': 'system', 'text': system}, {'id': uuid.uuid4().hex, 'role': 'user', 'text': user}], variables=[], vision={'enabled': bool(vision_selector), 'configs': {'detail': 'high', 'variable_selector': vision_selector or []}})

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

    def http(self, title, url, params='', cad=False):
        auth = 'CAD_API_KEY' if cad else 'MONGODB_API_KEY'
        return self.node('http-request', title, method='post' if cad else 'get', url=url, authorization={'type': 'api-key', 'config': {'type': 'custom', 'api_key': '{{#env.' + auth + '#}}', 'header': 'X-API-Key'}}, headers='Content-Type:application/json' if cad else 'Accept:application/json', params=params, body={'type': 'none', 'data': []}, timeout={'connect': 5, 'read': 90 if cad else 15, 'write': 30 if cad else 5}, ssl_verify=True, retry_config={'max_retries': 0, 'retry_enabled': False, 'retry_interval': 1000})
