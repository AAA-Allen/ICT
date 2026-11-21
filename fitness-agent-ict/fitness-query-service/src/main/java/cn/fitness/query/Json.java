package cn.fitness.query;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Collections;

/** 仅处理本模块 JSON 边界；业务查询不拼接 JSON 字符串。 */
public final class Json {
    private Json() { }

    public static Object parse(String source) {
        Parser parser = new Parser(source);
        Object value = parser.value(0);
        parser.space();
        if (parser.position != source.length()) throw parser.error();
        return value;
    }

    public static String stringify(Object value) {
        StringBuilder out = new StringBuilder();
        write(value, out);
        return out.toString();
    }

    public static Object freeze(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> copy = new LinkedHashMap<>();
            map.forEach((key, item) -> copy.put((String) key, freeze(item)));
            return Collections.unmodifiableMap(copy);
        }
        if (value instanceof List<?> list) return list.stream().map(Json::freeze).toList();
        return value;
    }

    private static void write(Object value, StringBuilder out) {
        if (value == null) out.append("null");
        else if (value instanceof String string) {
            out.append('"');
            for (char ch : string.toCharArray()) {
                switch (ch) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (ch < 32) out.append(String.format("\\u%04x", (int) ch));
                        else out.append(ch);
                    }
                }
            }
            out.append('"');
        } else if (value instanceof Boolean) out.append(value);
        else if (value instanceof Number number) {
            if (!Double.isFinite(number.doubleValue())) throw new IllegalArgumentException("Non-finite JSON number");
            out.append(value);
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (var entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException("JSON key must be string");
                if (!first) out.append(',');
                first = false;
                write(entry.getKey(), out);
                out.append(':');
                write(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable<?> items) {
            out.append('[');
            boolean first = true;
            for (Object item : items) {
                if (!first) out.append(',');
                first = false;
                write(item, out);
            }
            out.append(']');
        } else throw new IllegalArgumentException("Unsupported JSON value");
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Object value) {
        if (!(value instanceof Map<?, ?>)) throw new IllegalArgumentException("Expected JSON object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> array(Object value) {
        if (!(value instanceof List<?>)) throw new IllegalArgumentException("Expected JSON array");
        return (List<Object>) value;
    }

    private static final class Parser {
        private final String source;
        private int position;
        private Parser(String source) { this.source = source; }
        private IllegalArgumentException error() { return new IllegalArgumentException("Invalid JSON at " + position); }
        private void space() {
            while (position < source.length() && " \r\n\t".indexOf(source.charAt(position)) >= 0) position++;
        }
        private boolean take(char ch) {
            if (position < source.length() && source.charAt(position) == ch) { position++; return true; }
            return false;
        }
        private Object value(int depth) {
            if (depth > 32) throw error();
            space();
            if (position >= source.length()) throw error();
            char ch = source.charAt(position);
            if (ch == '"') return string();
            if (take('{')) {
                Map<String, Object> map = new LinkedHashMap<>();
                space();
                if (take('}')) return map;
                do {
                    space();
                    if (position >= source.length() || source.charAt(position) != '"') throw error();
                    String key = string();
                    space();
                    if (!take(':') || map.containsKey(key)) throw error();
                    map.put(key, value(depth + 1));
                    space();
                    if (take('}')) return map;
                } while (take(','));
                throw error();
            }
            if (take('[')) {
                List<Object> list = new ArrayList<>();
                space();
                if (take(']')) return list;
                do {
                    list.add(value(depth + 1));
                    space();
                    if (take(']')) return list;
                } while (take(','));
                throw error();
            }
            for (String literal : List.of("true", "false", "null")) {
                if (source.startsWith(literal, position)) {
                    position += literal.length();
                    return literal.equals("null") ? null : Boolean.valueOf(literal);
                }
            }
            int start = position;
            take('-');
            if (!take('0')) { digits(true); }
            if (take('.')) digits(true);
            if (take('e') || take('E')) { if (!take('+')) take('-'); digits(true); }
            if (start == position) throw error();
            try {
                double number = Double.parseDouble(source.substring(start, position));
                if (!Double.isFinite(number)) throw error();
                return number;
            } catch (NumberFormatException exception) { throw error(); }
        }
        private void digits(boolean required) {
            int start = position;
            while (position < source.length() && source.charAt(position) >= '0' && source.charAt(position) <= '9') position++;
            if (required && start == position) throw error();
        }
        private String string() {
            if (!take('"')) throw error();
            StringBuilder out = new StringBuilder();
            while (position < source.length()) {
                char ch = source.charAt(position++);
                if (ch == '"') return out.toString();
                if (ch < 32) throw error();
                if (ch != '\\') { out.append(ch); continue; }
                if (position >= source.length()) throw error();
                char escaped = source.charAt(position++);
                switch (escaped) {
                    case '"', '\\', '/' -> out.append(escaped);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (position + 4 > source.length()) throw error();
                        try { out.append((char) Integer.parseInt(source.substring(position, position + 4), 16)); }
                        catch (NumberFormatException exception) { throw error(); }
                        position += 4;
                    }
                    default -> throw error();
                }
            }
            throw error();
        }
    }
}
