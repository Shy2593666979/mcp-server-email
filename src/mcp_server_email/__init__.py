import argparse
import asyncio
import server


def main():
    """Main entry point for the package."""
    parser = argparse.ArgumentParser("Search files in a specified directory")
    parser.add_argument("--dir", type=str, help="Specify the directory where attachments are located.")

    args = parser.parse_args()

    asyncio.run(server.serve(args.dir))

if __name__ == "__main__":
    main()
