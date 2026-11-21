package cn.fitness.query;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import java.io.IOException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** HTTP 适配层：只处理参数/响应，数据均来自启动时的本地资料快照。 */
public final class QueryHandler implements HttpHandler {
    private final Catalog catalog;
    private final Set<String> allowedOrigins;

    public QueryHandler(Catalog catalog, Set<String> allowedOrigins) {
        this.catalog = catalog;
        this.allowedOrigins = Set.copyOf(allowedOrigins);
    }

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        String requestId = UUID.randomUUID().toString();
        exchange.getResponseHeaders().set("X-Request-Id", requestId);
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
        exchange.getResponseHeaders().set("Vary", "Origin");
        try {
            String origin = exchange.getRequestHeaders().getFirst("Origin");
            if (origin != null) {
                if (!allowedOrigins.contains(origin)) throw new ApiException(403, "ORIGIN_NOT_ALLOWED", "当前网页来源不在本地允许列表中。", "Origin");
                exchange.getResponseHeaders().set("Access-Control-Allow-Origin", origin);
            }
            exchange.getResponseHeaders().set("Allow", "GET, OPTIONS");
            if (exchange.getRequestMethod().equals("OPTIONS")) {
                exchange.getResponseHeaders().set("Access-Control-Allow-Methods", "GET, OPTIONS");
                exchange.getResponseHeaders().set("Access-Control-Allow-Headers", "Content-Type");
                exchange.sendResponseHeaders(204, -1);
                return;
            }
            if (!exchange.getRequestMethod().equals("GET")) throw new ApiException(405, "METHOD_NOT_ALLOWED", "此资料服务只支持 GET 查询。", "method");
            if (exchange.getRequestURI().toString().length() > 4096) throw new ApiException(414, "URI_TOO_LONG", "查询地址过长。", "query");
            Map<String, String> query = parseQuery(exchange.getRequestURI().getRawQuery());
            String path = exchange.getRequestURI().getPath();
            if (path.length() > 1 && path.endsWith("/")) path = path.substring(0, path.length() - 1);
            if (path.equals("/api/v1/health")) {
                checkKeys(query, Set.of());
                send(exchange, 200, envelope(Map.of("status", "ok", "service", "fitness-query-service", "counts", catalog.counts()), Map.of(), requestId));
                return;
            }
            if (path.equals("/api/v1/catalog")) {
                checkKeys(query, Set.of());
                send(exchange, 200, envelope(catalog.snapshot(), Map.of("counts", catalog.counts()), requestId));
                return;
            }
            String[] parts = path.split("/");
            if (parts.length < 4 || !parts[1].equals("api") || !parts[2].equals("v1") ||
                !Set.of("muscles", "exercises", "demonstrations").contains(parts[3])) throw notFound();
            String collection = parts[3];
            if (parts.length == 5) {
                checkKeys(query, Set.of());
                Object item = catalog.entries(collection).stream().filter(entry -> parts[4].equals(entry.get("id"))).findFirst().orElseThrow(QueryHandler::notFound);
                send(exchange, 200, envelope(item, Map.of(), requestId));
            } else if (parts.length == 4) {
                send(exchange, 200, list(collection, query, requestId));
            } else throw notFound();
        } catch (ApiException exception) {
            send(exchange, exception.status, Map.of("error", Map.of("code", exception.code, "message", exception.getMessage(), "field", exception.field), "requestId", requestId));
        } catch (RuntimeException exception) {
            System.err.println("Query error " + requestId + ": " + exception.getClass().getSimpleName());
            send(exchange, 500, Map.of("error", Map.of("code", "INTERNAL_ERROR", "message", "本地资料查询暂不可用。"), "requestId", requestId));
        } finally { exchange.close(); }
    }

    private Map<String, Object> list(String collection, Map<String, String> query, String requestId) {
        Set<String> keys = switch (collection) {
            case "muscles" -> Set.of("q", "page", "pageSize");
            case "exercises" -> Set.of("muscleId", "goal", "level", "equipment", "q", "page", "pageSize");
            default -> Set.of("exerciseId", "issueCode", "severity", "q", "page", "pageSize");
        };
        checkKeys(query, keys);
        String keyword = query.getOrDefault("q", "").strip().toLowerCase(Locale.ROOT);
        if (keyword.length() > 100) throw bad("q", "关键词不能超过 100 个字符。");
        checkId(query, "muscleId", "muscles");
        checkId(query, "exerciseId", "exercises");
        checkEnum(query, "goal", Catalog.GOALS);
        checkEnum(query, "level", Set.copyOf(Catalog.LEVELS));
        checkEnum(query, "equipment", Catalog.EQUIPMENT);
        checkEnum(query, "severity", Catalog.SEVERITIES);
        if (query.containsKey("issueCode") && !query.get("issueCode").matches("[A-Z][A-Z0-9_]{0,99}")) throw bad("issueCode", "问题代码格式无效。");
        int page = integer(query, "page", 1, 100_000);
        int pageSize = integer(query, "pageSize", 20, 100);
        List<Map<String, Object>> filtered = catalog.entries(collection).stream().filter(item -> {
            if (!keyword.isEmpty() && !searchText(item).contains(keyword)) return false;
            if (collection.equals("exercises")) {
                if (query.containsKey("muscleId") && !Json.array(item.get("targetMuscles")).contains(query.get("muscleId"))) return false;
                if (query.containsKey("goal") && !Json.array(item.get("goals")).contains(query.get("goal"))) return false;
                if (query.containsKey("level") && Catalog.LEVELS.indexOf(item.get("difficulty")) > Catalog.LEVELS.indexOf(query.get("level"))) return false;
                if (query.containsKey("equipment") && !item.get("equipment").equals("none") && !item.get("equipment").equals(query.get("equipment"))) return false;
            }
            if (collection.equals("demonstrations")) {
                if (query.containsKey("exerciseId") && !item.get("exerciseId").equals(query.get("exerciseId"))) return false;
                if (query.containsKey("issueCode") && !item.get("issueCode").equals(query.get("issueCode"))) return false;
                if (query.containsKey("severity") && !Json.array(item.get("severities")).contains(query.get("severity"))) return false;
            }
            return true;
        }).toList();
        int from = (int) Math.min((long) (page - 1) * pageSize, filtered.size());
        int to = Math.min(from + pageSize, filtered.size());
        return envelope(filtered.subList(from, to), Map.of("page", page, "pageSize", pageSize, "total", filtered.size(), "totalPages", (filtered.size() + pageSize - 1) / pageSize), requestId);
    }

    private String searchText(Map<String, Object> item) {
        return List.of("id", "name", "englishName", "title", "description", "issueCode").stream()
            .map(key -> String.valueOf(item.getOrDefault(key, ""))).reduce("", (a, b) -> a + " " + b).toLowerCase(Locale.ROOT);
    }

    private void checkId(Map<String, String> query, String key, String collection) {
        if (query.containsKey(key) && !catalog.hasId(collection, query.get(key))) throw bad(key, "未知资料 ID：" + key);
    }
    private static void checkEnum(Map<String, String> query, String key, Set<String> allowed) {
        if (query.containsKey(key) && !allowed.contains(query.get(key))) throw bad(key, "不支持的参数值：" + key);
    }
    private static int integer(Map<String, String> query, String key, int fallback, int max) {
        if (!query.containsKey(key)) return fallback;
        String value = query.get(key);
        if (!value.matches("[1-9][0-9]{0,5}")) throw bad(key, "分页参数必须是正整数。");
        int result = Integer.parseInt(value);
        if (result > max) throw bad(key, "分页参数超出允许范围。");
        return result;
    }
    private static void checkKeys(Map<String, String> query, Set<String> allowed) {
        for (String key : query.keySet()) if (!allowed.contains(key)) throw bad(key, "不支持的查询字段：" + key);
    }
    private static Map<String, String> parseQuery(String raw) {
        Map<String, String> query = new LinkedHashMap<>();
        if (raw == null || raw.isEmpty()) return query;
        for (String part : raw.split("&", -1)) {
            String[] pair = part.split("=", 2);
            String key;
            String value;
            try {
                key = URLDecoder.decode(pair[0], StandardCharsets.UTF_8);
                value = pair.length == 2 ? URLDecoder.decode(pair[1], StandardCharsets.UTF_8) : "";
            } catch (IllegalArgumentException exception) { throw bad("query", "查询编码无效。"); }
            if (key.isBlank() || query.containsKey(key)) throw bad(key, "查询字段为空或重复。");
            query.put(key, value);
        }
        return query;
    }
    private static Map<String, Object> envelope(Object data, Map<String, Object> extra, String requestId) {
        Map<String, Object> meta = new LinkedHashMap<>(extra);
        meta.put("source", "local_catalog");
        meta.put("contractVersion", Catalog.CONTRACT_VERSION);
        return Map.of("data", data, "meta", meta, "requestId", requestId);
    }
    private static void send(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = Json.stringify(body).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        if (exchange.getRequestMethod().equals("HEAD")) { exchange.sendResponseHeaders(status, -1); return; }
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }
    private static ApiException bad(String field, String message) { return new ApiException(400, "INVALID_QUERY", message, field); }
    private static ApiException notFound() { return new ApiException(404, "NOT_FOUND", "未找到该资料或查询路径。", "path"); }
    private static final class ApiException extends RuntimeException {
        final int status;
        final String code;
        final String field;
        ApiException(int status, String code, String message, String field) { super(message); this.status = status; this.code = code; this.field = field; }
    }
}
