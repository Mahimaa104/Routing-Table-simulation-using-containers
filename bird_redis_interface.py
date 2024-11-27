import subprocess
import redis
import socket
import time
import re

class BirdRedisInterface:
    def __init__(self, redis_host='redis', redis_port=6379, redis_db=0):
        self.redis_client = redis.Redis(host=redis_host, port=redis_port, db=redis_db)
        self.hostname = socket.gethostname()
        self.container_id = self.hostname

    def get_bird_routes(self):
        try:
            result = subprocess.run(['birdc', 'show', 'route'], capture_output=True, text=True)
            print(f"BIRD route command output: {result.stdout}")
            return result.stdout
        except subprocess.CalledProcessError as e:
            print(f"Error running BIRD route command: {e}")
            return None

    def parse_routes(self, route_output):
        routes = []
        current_route = {}
        for line in route_output.split('\n'):
            line = line.strip()
            if not line or line.startswith('BIRD') or line.startswith('Table'):
                continue
            if re.match(r'^\S', line):  # New route entry
                if current_route:
                    routes.append(current_route)
                    current_route = {}
                parts = line.split()
                if len(parts) >= 6:
                    current_route['network'] = parts[0]
                    current_route['type'] = parts[1]
                    current_route['source_protocol'] = parts[2][1:-1]  # Remove brackets
                    current_route['timestamp'] = parts[3]
                    current_route['preference'] = int(parts[5][1:-1])  # Remove parentheses and convert to int
            elif line.startswith('\t'):  # Route details
                parts = line.split()
                if parts[0] == 'via' and len(parts) >= 4:
                    current_route['next_hop'] = parts[1]
                    current_route['interface'] = parts[3]
                elif parts[0] == 'dev' and len(parts) >= 2:
                    current_route['interface'] = parts[1]
        
        if current_route:
            routes.append(current_route)
        
        return {"routes": routes}

    def send_routes_to_redis(self, routes):
        pipeline = self.redis_client.pipeline()
        for route in routes["routes"]:
            # Remove unwanted fields
            route = {k: v for k, v in route.items() if k not in ['Flags', 'MSS', 'Window', 'irtt']}
            key = f"route:{self.container_id}:{route['network']}"
            for field, value in route.items():
                pipeline.hset(key, field, value)
            pipeline.expire(key, 60)  # Set expiry to 60 seconds
        pipeline.execute()
        print(f"Sent {len(routes['routes'])} routes to Redis from container {self.container_id}")

    def run(self):
        while True:
            print("Fetching BIRD routes...")
            route_output = self.get_bird_routes()
            if route_output:
                print("Routes fetched successfully, parsing...")
                routes = self.parse_routes(route_output)
                print("Sending routes to Redis...")
                self.send_routes_to_redis(routes)
            else:
                print("No routes found or error in getting routes")
            print("Waiting for 30 seconds before next update")
            time.sleep(30)

if __name__ == "__main__":
    try:
        print("Script started")
        interface = BirdRedisInterface()
        interface.run()
    except Exception as e:
        print(f"An error occurred: {e}")

