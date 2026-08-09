def run_skill(args, player=None):
    from actions.youtube_video import youtube_video_tool
    query = args.get('query')
    return youtube_video_tool.run(query=query, action='play')