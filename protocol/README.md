# Deskbot Protocol

协议目录由固件调度负责人和服务端集成负责人共同维护。任何一端都不得私自添加只对自己可见的消息格式。

## MVP 传输

- 控制通道：WebSocket。
- 文本编码：UTF-8 JSON。
- 协议版本：`0.1`。
- 音频/视频的二进制帧、Opus 参数和 UDP 通道尚未冻结；在形成端到端测量前不写入稳定协议。

## 通用信封

每条控制消息包含：

| 字段 | 含义 |
| --- | --- |
| `version` | 协议版本，当前为 `0.1` |
| `type` | 消息类型，如 `device.command` |
| `id` | 本条消息的唯一 ID |
| `timestamp_ms` | Unix 毫秒时间戳 |
| `correlation_id` | 可选；响应所对应请求的 `id` |
| `payload` | 与消息类型相关的对象 |

当前 schema 只冻结信封，不冻结具体命令集合。命令白名单在第一次固件/服务端联合评审后单独加入。

## 兼容规则

- 接收端必须拒绝不支持的主版本。
- 未知 `type` 返回明确错误，不执行默认动作。
- 新增可选字段属于向后兼容；删除字段或改变语义必须提升版本。
- `device.command` 的结果用 `device.event` 返回，并设置 `correlation_id`。

## 示例

- [设备命令](examples/device-command.json)
- [设备事件](examples/device-event.json)
- [JSON Schema](schema/envelope.schema.json)
