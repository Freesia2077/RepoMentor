# RepoMentor 运行记录

Node customAlphabet('ab', 4) 返回的生成器，分别不传长度、传 2、传 0 时返回多长？长度 2 会被 defaultSize 强行改为 4 吗？沿参数与截取路径说明。

状态：success；新增模型请求：3；耗时：29.047 秒

Node customAlphabet('ab', 4) 返回的生成器：不传长度时返回 4 个字符；传 2 时返回 2 个字符；传 0 时返回空字符串。长度 2 不会被 defaultSize 强行改为 4。

## 引用源码

### index.js:96-175

```text
96: export function customAlphabet(alphabet, defaultSize = 21) {
97:   if (
98:     typeof alphabet !== 'string' ||
99:     !alphabet ||
100:     alphabet.length > 256
101:   ) {
102:     return customRandom(alphabet, defaultSize, random)
103:   }
104:   for (let i = 0; i < alphabet.length; i++) {
105:     if (alphabet.charCodeAt(i) > 255) {
106:       // The fast path below writes char codes to a byte buffer,
107:       // so it supports only single-byte characters.
108:       return customRandom(alphabet, defaultSize, random)
109:     }
110:   }
111: 
112:   // Alphabet char codes to write IDs into a byte buffer directly.
113:   let charCodes = Uint8Array.from(alphabet, str => {
114:     return str.charCodeAt(0)
115:   })
116:   let alphabetLen = alphabet.length
117:   // The smallest `2^n - 1` covering all alphabet indexes. Rejecting
118:   // `byte & mask >= alphabetLen` avoids modulo bias without `%` operation.
119:   let mask = (2 << (31 - Math.clz32((alphabetLen - 1) | 1))) - 1
120: 
121:   // IDs are pre-generated into a string pool: accepted alphabet chars are
122:   // written to a byte buffer, converted to a string with one
123:   // `Buffer#toString()` call, and every ID is a cheap `String#substring()`.
124:   // It avoids per-character string concatenations and pays string
125:   // allocation cost once per refill.
126:   //
127:   // The pool starts at the first requested size and grows geometrically,
128:   // so short-living generators do not pay for a full pool.
129:   let pool = ''
130:   let poolOffset = 0
131:   let poolNext = 0
132: 
133:   return (size = defaultSize) => {
134:     // `|=` convert `size` to number to prevent `valueOf` abusing
135:     // and pool offset pollution
136:     size |= 0
137:     if (size < 0) throw new RangeError('Wrong ID size')
138:     if (size === 0) return ''
139:     if (poolOffset + size > pool.length) {
140:       let target = Math.max(poolNext, size)
141:       poolNext = Math.min(target * 16, POOL_MAX)
142:       let buffer = Buffer.allocUnsafe(target)
143:       if (mask === alphabetLen - 1) {
144:         // Power-of-two alphabets like `urlAlphabet` accept every byte,
145:         // so random bytes are translated in place without rejections.
146:         fillRandom(buffer)
147:         for (let i = 0; i < target; i++) {
148:           buffer[i] = charCodes[buffer[i] & mask]
149:         }
150:       } else {
151:         // Rejection sampling accepts `alphabetLen` of every `mask + 1`
152:         // random bytes. `1.6` requests extra bytes to cover
153:         // unlucky streaks in most fills.
154:         let randomBytes = Buffer.allocUnsafe(
155:           Math.ceil((1.6 * (mask + 1) * target) / alphabetLen)
156:         )
157:         let accepted = 0
158:         while (accepted < target) {
159:           fillRandom(randomBytes)
160:           for (let i = 0; i < randomBytes.length; i++) {
161:             let index = randomBytes[i] & mask
162:             if (index < alphabetLen) {
163:               buffer[accepted++] = charCodes[index]
164:               if (accepted === target) break
165:             }
166:           }
167:         }
168:       }
169:       pool = buffer.toString('latin1')
170:       poolOffset = 0
171:     }
172:     poolOffset += size
173:     return pool.substring(poolOffset - size, poolOffset)
174:   }
175: }
```

## 工具轨迹

| 工具 | 状态 | 来源位置 |
| --- | --- | --- |
| read_code | ok | index.js:146-175 |

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
