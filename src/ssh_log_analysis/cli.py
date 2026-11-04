"""Command-line entry point for the SSH log experiment."""
import argparse

def main():
    parser = argparse.ArgumentParser(description="SSH log fine-tuning experiment")
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.parse_args()
    parser.print_help()
