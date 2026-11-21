package cn.fitness.query;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 只读资料仓库，在启动时校验并载入，不与算法或个人视频耦合。 */
public final class Catalog {
    public static final String CONTRACT_VERSION = "personal-v1";
    public static final List<String> LEVELS = List.of("beginner", "intermediate", "advanced");
    public static final Set<String> GOALS = Set.of("strength", "endurance", "general_fitness");
    public static final Set<String> EQUIPMENT = Set.of("none", "dumbbell", "resistance_band");
    public static final Set<String> SEVERITIES = Set.of("low", "medium", "high");
    private final Map<String, Object> data;

    private Catalog(Map<String, Object> data) {
        this.data = Json.object(Json.freeze(data));
        if (!CONTRACT_VERSION.equals(data.get("contractVersion"))) throw new IllegalArgumentException("Unsupported catalog version");
        Set<String> muscleIds = ids("muscles");
        Set<String> exerciseIds = ids("exercises");
        ids("demonstrations");
        for (Map<String, Object> muscle : entries("muscles")) {
            requiredText(muscle, "name"); requiredText(muscle, "description");
            strings(muscle, "primaryMuscles");
        }
        for (Map<String, Object> exercise : entries("exercises")) {
            requiredText(exercise, "name"); requiredText(exercise, "description");
            if (!muscleIds.containsAll(strings(exercise, "targetMuscles")) ||
                !LEVELS.contains(exercise.get("difficulty")) || !EQUIPMENT.contains(exercise.get("equipment")) ||
                !GOALS.containsAll(strings(exercise, "goals"))) throw new IllegalArgumentException("Invalid exercise references");
        }
        for (Map<String, Object> demo : entries("demonstrations")) {
            requiredText(demo, "title"); requiredText(demo, "description"); requiredText(demo, "issueCode");
            if (!exerciseIds.contains(demo.get("exerciseId")) || !SEVERITIES.containsAll(strings(demo, "severities"))) throw new IllegalArgumentException("Invalid demonstration references");
            if (demo.get("media") != null) {
                Map<String, Object> media = Json.object(demo.get("media"));
                if (!"playback_sample".equals(media.get("kind")) || !"playback-sample".equals(media.get("assetKey"))) throw new IllegalArgumentException("Unknown sample media");
                requiredText(media, "description");
            }
        }
    }

    public static Catalog load() throws IOException {
        try (var stream = Catalog.class.getResourceAsStream("/catalog.json")) {
            if (stream == null) throw new IOException("catalog.json is missing from the classpath");
            return new Catalog(Json.object(Json.parse(new String(stream.readAllBytes(), StandardCharsets.UTF_8))));
        }
    }

    static Catalog fromJson(String json) { return new Catalog(Json.object(Json.parse(json))); }
    public Map<String, Object> snapshot() { return data; }
    public List<Map<String, Object>> entries(String name) { return Json.array(data.get(name)).stream().map(Json::object).toList(); }
    public boolean hasId(String name, String id) { return entries(name).stream().anyMatch(item -> id.equals(item.get("id"))); }
    public Map<String, Object> counts() { return Map.of("muscles", entries("muscles").size(), "exercises", entries("exercises").size(), "demonstrations", entries("demonstrations").size()); }

    private Set<String> ids(String name) {
        Set<String> ids = new HashSet<>();
        for (Map<String, Object> entry : entries(name)) {
            String id = requiredText(entry, "id");
            if (!id.matches("[a-z][a-z0-9_-]{0,99}") || !ids.add(id)) throw new IllegalArgumentException("Invalid or duplicate catalog id");
        }
        if (ids.isEmpty()) throw new IllegalArgumentException("Empty catalog collection");
        return ids;
    }

    static String requiredText(Map<String, Object> data, String key) {
        Object value = data.get(key);
        if (!(value instanceof String text) || text.isBlank()) throw new IllegalArgumentException("Invalid field: " + key);
        return text;
    }

    static List<String> strings(Map<String, Object> data, String key) {
        List<Object> values = Json.array(data.get(key));
        if (values.isEmpty()) throw new IllegalArgumentException("Empty field: " + key);
        return values.stream().map(value -> {
            if (!(value instanceof String text) || text.isBlank()) throw new IllegalArgumentException("Invalid field: " + key);
            return text;
        }).toList();
    }
}
