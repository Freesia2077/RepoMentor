# RepoMentor 运行记录

这能证明该仓库的测试今天已经在我的电脑上执行通过了吗？如果不能，还缺什么？

状态：success；新增模型请求：2；耗时：21.703 秒

不能证明该仓库的测试今天已经在你的电脑上执行通过了。

## 引用源码

### test/index_test.ts:51-106

```text
51: 	describe('on()', () => {
52: 		it('should be a function', () => {
53: 			expect(inst).to.have.property('on').that.is.a('function');
54: 		});
55: 
56: 		it('should register handler for new type', () => {
57: 			const foo = () => {};
58: 			inst.on('foo', foo);
59: 
60: 			expect(events.get('foo')).to.deep.equal([foo]);
61: 		});
62: 
63: 		it('should register handlers for any type strings', () => {
64: 			const foo = () => {};
65: 			inst.on('constructor', foo);
66: 
67: 			expect(events.get('constructor')).to.deep.equal([foo]);
68: 		});
69: 
70: 		it('should append handler for existing type', () => {
71: 			const foo = () => {};
72: 			const bar = () => {};
73: 			inst.on('foo', foo);
74: 			inst.on('foo', bar);
75: 
76: 			expect(events.get('foo')).to.deep.equal([foo, bar]);
77: 		});
78: 
79: 		it('should NOT normalize case', () => {
80: 			const foo = () => {};
81: 			inst.on('FOO', foo);
82: 			inst.on('Bar', foo);
83: 			inst.on('baz:baT!', foo);
84: 
85: 			expect(events.get('FOO')).to.deep.equal([foo]);
86: 			expect(events.has('foo')).to.equal(false);
87: 			expect(events.get('Bar')).to.deep.equal([foo]);
88: 			expect(events.has('bar')).to.equal(false);
89: 			expect(events.get('baz:baT!')).to.deep.equal([foo]);
90: 		});
91: 
92: 		it('can take symbols for event types', () => {
93: 			const foo = () => {};
94: 			inst.on(eventType, foo);
95: 			expect(events.get(eventType)).to.deep.equal([foo]);
96: 		});
97: 
98: 		// Adding the same listener multiple times should register it multiple times.
99: 		// See https://nodejs.org/api/events.html#events_emitter_on_eventname_listener
100: 		it('should add duplicate listeners', () => {
101: 			const foo = () => {};
102: 			inst.on('foo', foo);
103: 			inst.on('foo', foo);
104: 			expect(events.get('foo')).to.deep.equal([foo, foo]);
105: 		});
106: 	});
```

### test/index_test.ts:108-158

```text
108: 	describe('off()', () => {
109: 		it('should be a function', () => {
110: 			expect(inst).to.have.property('off').that.is.a('function');
111: 		});
112: 
113: 		it('should remove handler for type', () => {
114: 			const foo = () => {};
115: 			inst.on('foo', foo);
116: 			inst.off('foo', foo);
117: 
118: 			expect(events.get('foo')).to.be.empty;
119: 		});
120: 
121: 		it('should NOT normalize case', () => {
122: 			const foo = () => {};
123: 			inst.on('FOO', foo);
124: 			inst.on('Bar', foo);
125: 			inst.on('baz:bat!', foo);
126: 
127: 			inst.off('FOO', foo);
128: 			inst.off('Bar', foo);
129: 			inst.off('baz:baT!', foo);
130: 
131: 			expect(events.get('FOO')).to.be.empty;
132: 			expect(events.has('foo')).to.equal(false);
133: 			expect(events.get('Bar')).to.be.empty;
134: 			expect(events.has('bar')).to.equal(false);
135: 			expect(events.get('baz:bat!')).to.have.lengthOf(1);
136: 		});
137: 
138: 		it('should remove only the first matching listener', () => {
139: 			const foo = () => {};
140: 			inst.on('foo', foo);
141: 			inst.on('foo', foo);
142: 			inst.off('foo', foo);
143: 			expect(events.get('foo')).to.deep.equal([foo]);
144: 			inst.off('foo', foo);
145: 			expect(events.get('foo')).to.deep.equal([]);
146: 		});
147: 
148: 		it('off("type") should remove all handlers of the given type', () => {
149: 			inst.on('foo', () => {});
150: 			inst.on('foo', () => {});
151: 			inst.on('bar', () => {});
152: 			inst.off('foo');
153: 			expect(events.get('foo')).to.deep.equal([]);
154: 			expect(events.get('bar')).to.have.length(1);
155: 			inst.off('bar');
156: 			expect(events.get('bar')).to.deep.equal([]);
157: 		});
158: 	});
```

### test/index_test.ts:163-203

```text
163: 		});
164: 
165: 		it('should invoke handler for type', () => {
166: 			const event = { a: 'b' };
167: 
168: 			inst.on('foo', (one, two?: unknown) => {
169: 				expect(one).to.deep.equal(event);
170: 				expect(two).to.be.an('undefined');
171: 			});
172: 
173: 			inst.emit('foo', event);
174: 		});
175: 
176: 		it('should NOT ignore case', () => {
177: 			const onFoo = spy(),
178: 				onFOO = spy();
179: 			events.set('Foo', [onFoo]);
180: 			events.set('FOO', [onFOO]);
181: 
182: 			inst.emit('Foo', 'Foo arg');
183: 			inst.emit('FOO', 'FOO arg');
184: 
185: 			expect(onFoo).to.have.been.calledOnce.and.calledWith('Foo arg');
186: 			expect(onFOO).to.have.been.calledOnce.and.calledWith('FOO arg');
187: 		});
188: 
189: 		it('should invoke * handlers', () => {
190: 			const star = spy(),
191: 				ea = { a: 'a' },
192: 				eb = { b: 'b' };
193: 
194: 			events.set('*', [star]);
195: 
196: 			inst.emit('foo', ea);
197: 			expect(star).to.have.been.calledOnce.and.calledWith('foo', ea);
198: 			star.resetHistory();
199: 
200: 			inst.emit('bar', eb);
201: 			expect(star).to.have.been.calledOnce.and.calledWith('bar', eb);
202: 		});
203: 	});
```

## 工具轨迹

| 工具 | 状态 | 来源位置 |
| --- | --- | --- |

## 质量边界

格式和来源校验不等于语义正确；以下仅为确定性诊断。

```json
{
  "version": "answer-diagnostics-v1",
  "issues": [],
  "semantic_correctness": "not_scored",
  "complete": null,
  "human_approved": false
}
```
