import subprocess
import redis
import socket
import time
import re
import sys
import argparse

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
            route = {k: v for k, v in route.items() if k in ['network', 'type', 'source_protocol', 'timestamp', 'preference']}
            key = f"route:{self.container_id}:{route['network']}"
            print(f"Preparing to set Redis key {key} with data {route}")  # Debug print
            for field, value in route.items():
                pipeline.hset(key, field, value)
            pipeline.expire(key, 60)  # Set expiry to 60 seconds
        pipeline.execute()
        print(f"Sent {len(routes['routes'])} routes to Redis from container {self.container_id}")

    def add_route(self, network, next_hop, interface, source_protocol='static', preference=200):
        # Add the route to BIRD
        add_route_cmd = f'echo "route {network} via {next_hop} on {interface} preference {preference} type {source_protocol};" | birdc configure'
        try:
            subprocess.run(add_route_cmd, shell=True, check=True)
            print(f"Added route {network} via {next_hop} on {interface} with preference {preference} and type {source_protocol}")
            
            # Update Redis
            key = f"route:{self.container_id}:{network}"
            route_data = {
                'network': network,
                'type': 'unicast',  # Assuming it's unicast
                'source_protocol': source_protocol,
                'timestamp': time.strftime("%H:%M:%S"),
                'preference': preference
            }
            print(f"Setting Redis key {key} with data {route_data}")  # Debug print
            self.redis_client.hset(key, mapping=route_data)
            self.redis_client.expire(key, 60)  # Set expiry to 60 seconds
            print(f"Updated Redis with new route: {key}")
        except subprocess.CalledProcessError as e:
            print(f"Error adding route: {e}")

    def delete_route(self, network):
        # Delete the route from BIRD
        delete_route_cmd = f'echo "unroute {network};" | birdc configure'
        try:
            subprocess.run(delete_route_cmd, shell=True, check=True)
            print(f"Deleted route {network}")
        except subprocess.CalledProcessError as e:
            print(f"Error deleting route: {e}")

    def lookup_route(self, network):
        key_pattern = f"route:{self.container_id}:{network}"
        keys = self.redis_client.keys(key_pattern)
        routes = []
        for key in keys:
            route = self.redis_client.hgetall(key)
            if route:  # Check if route is not empty
                print(f"Found route in Redis: {route}")  # Debug print
                routes.append(route)
            else:
                print(f"No data found for key: {key}")  # Debug print
        return routes

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
    parser = argparse.ArgumentParser(description='Manage BIRD routes')
    parser.add_argument('action', choices=['run', 'add', 'delete', 'lookup'], help='Action to perform')
    parser.add_argument('--network', help='Network address')
    parser.add_argument('--next-hop', help='Next hop address')
    parser.add_argument('--interface', help='Interface name')
    parser.add_argument('--source-protocol', default='static', help='Source protocol')
    parser.add_argument('--preference', type=int, default=200, help='Route preference')

    args = parser.parse_args()

    interface = BirdRedisInterface()

    if args.action == 'run':
        interface.run()
    elif args.action == 'add':
        if not all([args.network, args.next_hop, args.interface]):
            print("Error: network, next-hop, and interface are required for adding a route")
            sys.exit(1)
        interface.add_route(args.network, args.next_hop, args.interface, args.source_protocol, args.preference)
    elif args.action == 'delete':
        if not args.network:
            print("Error: network is required for deleting a route")
            sys.exit(1)
        interface.delete_route(args.network)
    elif args.action == 'lookup':
        if not args.network:
            print("Error: network is required for looking up a route")
            sys.exit(1)
        routes = interface.lookup_route(args.network)
        print(f"Lookup results: {routes}")
    else:
        print(f"Unknown action: {args.action}")
        sys.exit(1)

