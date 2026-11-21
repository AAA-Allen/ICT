package cn.fitness.query;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.Arrays;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.stream.Collectors;

public final class FitnessQueryApplication {
    private FitnessQueryApplication() { }

    public static void main(String[] args) {
        try {
            int port = 8081;
            for (String arg : args) {
                if (!arg.startsWith("--port=")) throw new IllegalArgumentException("Use --port=8081");
                port = Integer.parseInt(arg.substring(7));
            }
            if (port < 1024 || port > 65535) throw new IllegalArgumentException("Port must be 1024..65535");
            String origins = System.getenv().getOrDefault("FITNESS_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173");
            Set<String> allowedOrigins = Arrays.stream(origins.split(",")).map(String::strip).filter(value -> !value.isEmpty()).collect(Collectors.toSet());
            if (allowedOrigins.contains("*")) throw new IllegalArgumentException("Use explicit allowed origins");
            RunningServer running = start(Catalog.load(), port, allowedOrigins);
            Runtime.getRuntime().addShutdownHook(new Thread(running::close));
            System.out.println("Fitness Query Service: http://127.0.0.1:" + running.port() + "/api/v1/health");
            System.out.println("Read-only local catalog / personal-v1 / Ctrl+C to stop");
        } catch (Exception exception) {
            System.err.println("Could not start local query service: " + exception.getMessage());
            System.exit(1);
        }
    }

    static RunningServer start(Catalog catalog, int port, Set<String> allowedOrigins) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 32);
        ExecutorService workers = Executors.newFixedThreadPool(4);
        server.setExecutor(workers);
        server.createContext("/", new QueryHandler(catalog, allowedOrigins));
        server.start();
        return new RunningServer(server, workers);
    }

    static final class RunningServer implements AutoCloseable {
        private final HttpServer server;
        private final ExecutorService workers;
        RunningServer(HttpServer server, ExecutorService workers) { this.server = server; this.workers = workers; }
        int port() { return server.getAddress().getPort(); }
        @Override public void close() { server.stop(0); workers.shutdownNow(); }
    }
}
