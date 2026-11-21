package cn.fitness.query;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 无外部测试依赖；使用随机本地端口验证真实 HTTP，不占用用户 8081 服务。 */
public final class QueryServiceTest {
    private static int passed;
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();

    public static void main(String[] args) throws Exception {
        jsonChecks();
        Catalog catalog = Catalog.load();
        check(catalog.entries("muscles").size() == 11 && catalog.entries("exercises").size() == 21 && catalog.entries("demonstrations").size() == 3, "Catalog counts");
        try { catalog.snapshot().put("changed", true); throw new AssertionError("Mutable catalog"); }
        catch (UnsupportedOperationException expected) { passed++; }
        Map<String, Object> invalid = Json.object(Json.parse(Json.stringify(catalog.snapshot())));
        Json.object(Json.array(invalid.get("exercises")).get(0)).put("targetMuscles", List.of("unknown"));
        reject(() -> Catalog.fromJson(Json.stringify(invalid)), "Catalog reference validation");
        try (var running = FitnessQueryApplication.start(catalog, 0, Set.of("http://127.0.0.1:5173"))) {
            String base = "http://127.0.0.1:" + running.port();
            var health = get(base, "/api/v1/health");
            check(health.statusCode() == 200 && Json.object(body(health).get("data")).get("status").equals("ok"), "Health");
            check(health.headers().firstValue("Content-Type").orElse("").contains("utf-8") && health.headers().firstValue("Cache-Control").orElse("").equals("no-store"), "Encoding and cache headers");
            check(body(health).get("requestId").equals(health.headers().firstValue("X-Request-Id").orElse("")), "Request identity");
            check(Json.object(body(get(base, "/api/v1/catalog")).get("data")).get("contractVersion").equals("personal-v1"), "Full catalog contract");
            check(data(get(base, "/api/v1/muscles")).size() == 11, "Muscle list");
            check(Json.object(body(get(base, "/api/v1/muscles/quads")).get("data")).get("name").equals("股四头肌"), "Muscle detail Chinese");
            check(data(get(base, "/api/v1/muscles?q=" + URLEncoder.encode("大腿后", StandardCharsets.UTF_8))).size() == 1, "Unicode search");
            check(data(get(base, "/api/v1/muscles?q=pectorals")).size() == 1, "English case insensitive search");
            var squat = get(base, "/api/v1/exercises?muscleId=quads&goal=strength&level=beginner&equipment=none");
            check(ids(squat).equals(List.of("bodyweight_squat", "sit_to_stand")), "Default squat filtering");
            check(ids(get(base, "/api/v1/exercises?muscleId=quads&goal=strength&level=intermediate&equipment=dumbbell")).equals(List.of("bodyweight_squat", "reverse_lunge", "goblet_squat", "sit_to_stand")), "Available equipment includes bodyweight");
            check(ids(get(base, "/api/v1/exercises?muscleId=chest&level=beginner&equipment=none")).equals(List.of("wall_push_up")), "Level ceiling");
            check(Json.object(body(get(base, "/api/v1/exercises/bodyweight_squat")).get("data")).get("name").equals("徒手深蹲"), "Exercise detail");
            var paged = get(base, "/api/v1/exercises?page=2&pageSize=10");
            check(data(paged).size() == 10 && number(Json.object(body(paged).get("meta")).get("total")) == 21, "Pagination totals");
            check(data(get(base, "/api/v1/exercises?page=100000&pageSize=100")).isEmpty(), "Out of range page is empty");
            check(data(get(base, "/api/v1/exercises?q=NO_SUCH_MOVEMENT")).isEmpty(), "No search matches");
            var demo = get(base, "/api/v1/demonstrations?exerciseId=bodyweight_squat&issueCode=DEPTH_INSUFFICIENT&severity=medium");
            check(ids(demo).equals(List.of("squat-depth-example")), "Exact demonstration filters");
            check(Json.object(Json.object(data(demo).get(0)).get("media")).get("assetKey").equals("playback-sample") && !demo.body().contains("file:"), "Portable sample media metadata");
            check(Json.object(body(get(base, "/api/v1/demonstrations/squat-control-example")).get("data")).get("media") == null, "Matched metadata with missing material");
            check(data(get(base, "/api/v1/demonstrations?exerciseId=sit_to_stand&severity=low")).isEmpty(), "No unrelated demonstration fallback");
            for (String path : List.of("/api/v1/muscles/unknown", "/api/v1/exercises/unknown", "/api/v1/demonstrations/unknown", "/api/v1/nope", "/api/v1/health/extra")) {
                var response = get(base, path);
                check(response.statusCode() == 404 && Json.object(body(response).get("error")).get("code").equals("NOT_FOUND"), "404 " + path);
            }
            for (String query : List.of("level=expert", "muscleId=unknown", "equipment=barbell", "goal=weight_loss", "page=0", "page=-1", "page=1.5", "page=9999999999", "pageSize=101", "pageSize=", "limit=10", "q=a&q=b")) {
                var response = get(base, "/api/v1/exercises?" + query);
                check(response.statusCode() == 400 && Json.object(body(response).get("error")).get("code").equals("INVALID_QUERY"), "400 " + query);
            }
            check(get(base, "/api/v1/demonstrations?severity=critical").statusCode() == 400, "Invalid demonstration severity");
            check(get(base, "/api/v1/demonstrations?issueCode=lowercase").statusCode() == 400, "Invalid issue code");
            check(get(base, "/api/v1/health?unexpected=1").statusCode() == 400, "Health rejects unknown query");
            check(get(base, "/api/v1/muscles?q=" + "a".repeat(101)).statusCode() == 400, "Keyword limit");
            var post = request(base, "/api/v1/exercises", "POST", null);
            check(post.statusCode() == 405 && post.headers().firstValue("Allow").orElse("").contains("GET"), "Read-only method guard");
            check(request(base, "/api/v1/catalog", "HEAD", null).statusCode() == 405, "HEAD method guard without response body");
            var allowed = request(base, "/api/v1/catalog", "GET", "http://127.0.0.1:5173");
            check(allowed.statusCode() == 200 && allowed.headers().firstValue("Access-Control-Allow-Origin").orElse("").equals("http://127.0.0.1:5173"), "Allowed origin");
            var denied = request(base, "/api/v1/catalog", "GET", "https://unrelated.example");
            check(denied.statusCode() == 403 && denied.headers().firstValue("Access-Control-Allow-Origin").isEmpty(), "Denied origin");
            check(request(base, "/api/v1/catalog", "OPTIONS", "http://127.0.0.1:5173").statusCode() == 204, "Preflight");
        }
        System.out.println("Java checks passed: " + passed);
    }

    private static void jsonChecks() {
        List<Object> sample = new ArrayList<>();
        sample.add(Map.of("text", "中文\n\"quote\"\\path", "boolean", true, "number", -1.25e3)); sample.add(null);
        check(Json.stringify(Json.parse(Json.stringify(sample))).equals(Json.stringify(sample)), "JSON unicode and escapes round trip");
        check(Json.parse("[0,-2,1.5,1e2,true,false,null,\"\\u4e2d\"]") instanceof List<?>, "JSON value forms");
        for (String bad : List.of("{", "[1,]", "{\"a\":1,\"a\":2}", "01", "-", "1e", "1e999", "true false", "\"\\x\"", "[".repeat(40) + "0" + "]".repeat(40))) reject(() -> Json.parse(bad), "JSON rejects malformed input");
    }
    private static void reject(Runnable action, String label) { try { action.run(); throw new AssertionError(label); } catch (IllegalArgumentException expected) { passed++; } }
    private static void check(boolean condition, String label) { if (!condition) throw new AssertionError(label); passed++; }
    private static int number(Object value) { return ((Number) value).intValue(); }
    private static HttpResponse<String> get(String base, String path) throws Exception { return request(base, path, "GET", null); }
    private static HttpResponse<String> request(String base, String path, String method, String origin) throws Exception {
        var builder = HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofSeconds(5)).method(method, HttpRequest.BodyPublishers.noBody());
        if (origin != null) builder.header("Origin", origin);
        return CLIENT.send(builder.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }
    private static Map<String, Object> body(HttpResponse<String> response) { return Json.object(Json.parse(response.body())); }
    private static List<Object> data(HttpResponse<String> response) { return Json.array(body(response).get("data")); }
    private static List<String> ids(HttpResponse<String> response) { return data(response).stream().map(item -> (String) Json.object(item).get("id")).toList(); }
}
