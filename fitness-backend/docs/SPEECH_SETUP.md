# 健身语音提醒接入

本轮接入华为云 SIS 文本转语音（TTS），不是麦克风语音识别。真实模型调用需要开通对应区域的 SIS、有效项目 ID 和有权限的 IAM Token。

## 配置与启动

在启动后端的 PowerShell 中设置服务端环境变量。不要把真实 Token 提交仓库或发到聊天中。

```powershell
$env:FITNESS_SIS_REGION = 'cn-north-4'
$env:FITNESS_SIS_PROJECT_ID = '<对应区域项目ID>'
$env:FITNESS_SIS_TOKEN = '<有效IAM Token>'
$env:FITNESS_SIS_VOICE = 'chinese_xiaoyu_common'
.\.venv\Scripts\python.exe -m fitness_backend.server --port 8082
```

Token 过期后需要更新环境配置并重启后端。当前没有自动刷新 Token。端点按区域构造，实际区域是否支持所选声音以官方控制台为准。

`GET /api/health` 的 `speech_configured` 只表示本地配置齐全，不代表凭据已通过云端验证。

## 试听

启动已有前端，在徒手深蹲的后端分析区域点击“试听语音提醒”。已有 Agent 建议不超过 120 字时会显示“播报建议”。失败时保留文字与错误状态；没有浏览器 TTS 冒充云端模型的回退。

请求：`POST /api/speech`，沿用 `X-Session-Id`，JSON 为 `{"text":"请调整手机位置，确保全身进入画面。"}`。

返回 `audio_base64`、`mime_type=audio/mpeg` 和 `provider=huawei-sis`。文本限制 1–120 字；云端请求超时 8 秒，不自动重试，不返回云端错误原文或密钥。该接口沿用当前本机服务的访问边界，不是可直接公开部署的 API。

## 后续实时摄像头接入

可复用前端 `createCoachSpeech()`。只有动作事件通过连续帧、质量和机位门控后，才调用 `speak(短句)`；该方法不会判断动作是否正确。

当前播放模块缓存最多 20 条已合成短句，播放或合成期间丢弃新提示，同句 8 秒内不重复播放，`stop()` 取消合成等待和播放。停止/离开训练时应调用它。当前缓存只在该播放器实例内存在，第一遍仍需等待网络合成。

接入训练页时，应在开始训练的用户点击中解锁手机音频播放，并实测 iOS/Android 浏览器限制。本轮只有手动试听和建议播报，没有完成实时摄像头、自动事件触发、预合成或移动端自动播放验证。

固定纠错短句建议在训练开始前预合成，动态总结按需合成。过时的动作提醒应在业务层按事件时间和动作阶段丢弃，不能长队列依次播放。

## 验证范围

本轮用模拟云端响应检查协议、无配置、无效文本、损坏响应和鉴权失败，并运行现有后端测试及前端构建检查。未配置真实云端凭据，未验证真实音色、网络延迟或手机播放效果。

官方接口：https://support.huaweicloud.com/api-sis/sis_03_0111.html
