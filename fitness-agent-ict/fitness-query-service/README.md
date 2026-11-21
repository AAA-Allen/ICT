# Fitness Query Service

Java 只读资料服务，提供肌群、动作和示范资料元信息查询。采用 JDK 标准库 `jdk.httpserver`，编译目标为 JDK 17。

服务使用 `personal-v1` 协议，提供本地示例资料。

## 启动

在 PowerShell 执行：

```powershell
Set-Location .\fitness-query-service
.\run.cmd
```

启动脚本会先编译、打包，再监听 `127.0.0.1:8081`。保持终端打开，结束时按 `Ctrl+C`。脚本仅为当前进程允许执行项目的 PowerShell 文件，不修改系统执行策略。

健康检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8081/api/v1/health'
```

默认有 11 个肌群、21 个动作和 3 条示范资料。单独运行时可用 `.\run.cmd -Port 8082` 指定端口，并将前端的 `VITE_QUERY_PROXY_TARGET` 设为对应的服务地址。

## 构建与验证

```powershell
.\build.cmd
.\test.cmd
```

产物是 `build\fitness-query-service.jar`。测试使用随机本机端口，结束后自动关闭，不占用你的 8081 服务。

也可以直接运行已构建 JAR：

```powershell
java --add-modules jdk.httpserver -jar .\build\fitness-query-service.jar --port=8081
```

同时启动前端和 Java 服务时，使用[仓库根目录的启动命令](../README.md)。

## 查询入口

| 路径                          | 用途                     |
| ----------------------------- | ------------------------ |
| `/api/v1/health`              | 状态与资料数量           |
| `/api/v1/catalog`             | 前端一次读取全部资料快照 |
| `/api/v1/muscles`             | 肌群列表、搜索和分页     |
| `/api/v1/muscles/{id}`        | 单个肌群                 |
| `/api/v1/exercises`           | 动作条件查询、搜索和分页 |
| `/api/v1/exercises/{id}`      | 单个动作                 |
| `/api/v1/demonstrations`      | 示范资料匹配查询与分页   |
| `/api/v1/demonstrations/{id}` | 单条示范资料元信息       |

参数、状态码与响应示例见 [个人查询协议](docs/api-contract.md)。

## 资料同步

当前编辑源为前端 `src/data` 中的三个 TypeScript 数据文件。Java 使用打包进 JAR 的 `src/main/resources/catalog.json` 快照，编译和启动不会自动覆盖这份文件。

修改前端资料后，从本目录显式执行：

```powershell
node .\scripts\sync-data.mjs
node .\scripts\sync-data.mjs --check
.\test.cmd
```

同步脚本使用现有 Node 22.18+ / 24.12+ 的 TypeScript 类型擦除能力；当前项目数据只有可擦除类型和普通对象。它不使用正则表达式解析代码。示范资料中的前端资源地址被转换成便携的 `assetKey`，不会把电脑文件地址写进 API。

改完需要重启正在运行的 Java 服务，前端再点击“重新读取 Java 资料”。直接修改 JSON 适合临时试验；再次同步会用前端编辑源覆盖它。长期修改请回到前端源文件。

## 结构

```text
src/main/java/cn/fitness/query/
  FitnessQueryApplication.java  启动、端口、监听地址与线程清理
  QueryHandler.java             路由、参数、分页、错误、UTF-8 与 CORS
  Catalog.java                  资料载入、引用校验与只读快照
  Json.java                     JSON 边界读写
src/main/resources/catalog.json 本地资料快照
src/test/java/.../QueryServiceTest.java 真实 HTTP 与 JSON 检查
scripts/                        编译、运行、测试与资料同步
docs/api-contract.md            可替换的个人协议
```

## 跨域与部署边界

服务只监听本机回环地址。默认允许的网页来源为 `http://127.0.0.1:5173`、`http://localhost:5173` 及对应的 4173 预览地址。前端开发代理通过同源 `/api/v1` 访问本服务。

如果另一个本地网页确实需要直接跨域访问，可在启动终端设置本进程的 `FITNESS_ALLOWED_ORIGINS` 为逗号分隔的完整来源，保持精确地址。服务不启用通配来源或凭证跨域。

`vite preview` 的代理可用于本机生产构建验收。将 `dist/` 放到其他静态服务器时，需要为 `/api/v1` 配置自己的代理；当前没有做云部署。

## 官方参考

- [JDK 17 HttpServer](https://docs.oracle.com/en/java/javase/17/docs/api/jdk.httpserver/com/sun/net/httpserver/HttpServer.html)
- [javac 与 --release](https://docs.oracle.com/en/java/javase/17/docs/specs/man/javac.html)
- [Node.js TypeScript 类型擦除](https://nodejs.org/api/typescript.html)
