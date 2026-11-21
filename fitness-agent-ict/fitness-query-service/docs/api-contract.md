# 个人查询协议 personal-v1

本协议用于独立个人模块，不宣称已经与队友最终确认。服务仅提供本地示例资料；不接收、上传、识别或评分个人视频。

默认基础地址：`http://127.0.0.1:8081/api/v1`。业务查询使用 GET；OPTIONS 用于跨域预检。

## 统一响应

成功示例：

```json
{
  "data": [],
  "meta": {
    "source": "local_catalog",
    "contractVersion": "personal-v1",
    "page": 1,
    "pageSize": 20,
    "total": 0,
    "totalPages": 0
  },
  "requestId": "由服务生成的 UUID"
}
```

列表接口带分页字段；详情接口的 data 为单个对象，不带分页字段。`/catalog` 返回包含 `contractVersion`、`muscles`、`exercises`、`demonstrations` 的对象，meta 包含三类资料 counts。`/health` 返回 status、service 与 counts。

所有响应为 UTF-8 JSON，包含 `X-Request-Id`、`Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`。请求 ID 不回显用户输入。

错误示例（HTTP 400）：

```json
{
  "error": {
    "code": "INVALID_QUERY",
    "message": "不支持的参数值：level",
    "field": "level"
  },
  "requestId": "由服务生成的 UUID"
}
```

## 通用列表参数

| 参数       | 规则                                                      |
| ---------- | --------------------------------------------------------- |
| `q`        | 可选，忽略首尾空格和英文大小写；最多 100 个字符；支持中文 |
| `page`     | 可选，默认 1；正整数，最大 100000                         |
| `pageSize` | 可选，默认 20；正整数，最大 100                           |

结果按资料文件中的稳定顺序返回。超出最后一页时返回空列表，total 保留匹配总数；没有匹配时 totalPages 为 0。重复参数、未知参数和无效枚举返回 400，不会静默忽略。

q 搜索 id、name、englishName、title、description 和 issueCode 中存在的字段。

## 肌群

`GET /muscles` 支持通用参数。`GET /muscles/{id}` 返回单条。

id：`chest`、`deltoids`、`trapezius`、`lats`、`biceps`、`triceps`、`abs`、`glutes`、`quads`、`hamstrings`、`calves`。

字段：id、name、englishName、icon、caption、description、primaryMuscles。icon 仍复用前端六个身体大区的图标，不改变 11 个业务肌群 ID。

## 动作

`GET /exercises` 除通用参数外支持：

| 参数        | 允许值或行为                                                    |
| ----------- | --------------------------------------------------------------- |
| `muscleId`  | 上述已知肌群 ID，匹配 targetMuscles                             |
| `goal`      | `strength`、`endurance`、`general_fitness`                      |
| `level`     | `beginner`、`intermediate`、`advanced`；包含该等级以下的动作    |
| `equipment` | `none`、`dumbbell`、`resistance_band`；有器械时也包含无器械动作 |

条件可组合，未传入的字段不筛选。示例：

```text
/exercises?muscleId=quads&goal=strength&level=beginner&equipment=none
```

应返回 `bodyweight_squat` 和 `sit_to_stand`。`GET /exercises/{id}` 获取单条。

字段：id、name、targetMuscles、difficulty、equipment、goals、description。difficulty 是动作本身的样例难度，level 是查询者的经验上限。

## 示范资料

`GET /demonstrations` 除通用参数外支持 exerciseId、issueCode、severity；三者为精确匹配。exerciseId 必须存在；issueCode 使用大写字母、数字与下划线，未知但格式正确的代码返回空结果；severity 为 low / medium / high。

```text
/demonstrations?exerciseId=bodyweight_squat&issueCode=DEPTH_INSUFFICIENT&severity=medium
```

`GET /demonstrations/{id}` 获取单条。字段为 id、exerciseId、issueCode、severities、title、description、media。

media 可为 null，表示已有资料条目但没有视频。非空时当前仅有：

```json
{
  "kind": "playback_sample",
  "assetKey": "playback-sample",
  "description": "无声文字与动态图形，仅用于播放器测试，不包含动作示范。"
}
```

此服务查询元信息，不提供媒体文件下载接口。前端将已知 assetKey 适配到自己打包的测试片段，不信任任意远程媒体地址。正式教学素材仍待补充。

置信度来自分析结果，属于前端当前的匹配入口控制；低置信度时前端不发起纠错资料匹配。资料查询本身不接收置信度，不声称进行动作判断。

## 状态码

| HTTP | code               | 情况                               |
| ---- | ------------------ | ---------------------------------- |
| 200  | 成功响应           | 查询、详情或健康检查成功           |
| 204  | 无响应体           | 允许来源的预检                     |
| 400  | INVALID_QUERY      | 无效枚举、分页、重复或未知查询字段 |
| 403  | ORIGIN_NOT_ALLOWED | 不允许的网页 Origin                |
| 404  | NOT_FOUND          | 未知路径或详情 ID                  |
| 405  | METHOD_NOT_ALLOWED | 非 GET / OPTIONS 方法              |
| 414  | URI_TOO_LONG       | 查询地址超过 4096 字符             |
| 500  | INTERNAL_ERROR     | 内部查询错误，不返回堆栈           |

请求编码在进入处理器前被 JDK 拒绝时，可能由 JDK 返回协议级错误。当前无鉴权、数据库、写接口、视频上传、任务创建或历史账号接口。
