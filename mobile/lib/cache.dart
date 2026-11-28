import 'dart:async';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'api.dart';

/// Session-private, bounded prefetch cache. Current audio may use HTTPS streaming.
class AudioCache {
  final LainApi api;
  final Directory root;
  static const maxFile=200*1024*1024;
  static const maxCache=1024*1024*1024;
  final Map<int,Future<File?>> _jobs={};
  final Set<http.Client> _clients={};
  bool _closed=false;
  AudioCache._(this.api,this.root);
  static Future<AudioCache> create(LainApi api) async {
    if(!RegExp(r'^[a-f0-9]{32}$').hasMatch(api.deviceId))throw const FormatException('Invalid device');
    final base=await getTemporaryDirectory();
    final dir=Directory('${base.path}/lain-${api.deviceId}');
    await dir.create(recursive:true);
    return AudioCache._(api,dir);
  }
  static const extensions={'audio/mpeg':'mp3','audio/mp4':'m4a','audio/flac':'flac','audio/ogg':'ogg'};
  Future<File?> find(int id) async {
    for(final ext in extensions.values){
      final f=File('${root.path}/$id.$ext');
      if(await f.exists()&&await f.length()>0){
        await f.setLastModified(DateTime.now());
        return f;
      }
    }
    return null;
  }
  Future<File?> fetch(Track track,{Set<int> protected=const {}}) {
    if(_closed||!track.playable)return Future.value(null);
    return _jobs.putIfAbsent(track.id,()=>_fetch(track,protected).whenComplete(()=>_jobs.remove(track.id)));
  }
  Future<File?> _fetch(Track t,Set<int> protected) async {
    final existing=await find(t.id);
    if(existing!=null)return existing;
    final temp=File('${root.path}/${t.id}.part');
    final client=http.Client();_clients.add(client);
    IOSink? sink;
    try {
      final request=http.Request('GET',api.uri('/v1/tracks/${t.id}/audio'))
        ..followRedirects=false..headers.addAll(api.headers);
      final response=await client.send(request).timeout(const Duration(seconds:15));
      final ext=extensions[response.headers['content-type']?.split(';').first.trim()];
      final expected=response.contentLength;
      if(response.statusCode!=200||ext==null||expected==null||expected<=0||expected>maxFile)return null;
      await _trim(expected,{...protected,t.id});
      sink=temp.openWrite();
      var size=0;
      final deadline=DateTime.now().add(const Duration(minutes:3));
      await for(final block in response.stream.timeout(const Duration(seconds:20))){
        if(_closed||DateTime.now().isAfter(deadline))throw const FileSystemException('Download cancelled');
        size+=block.length;
        if(size>expected||size>maxFile)throw const FileSystemException('File too large');
        sink.add(block);
        await sink.flush();
      }
      await sink.close();sink=null;
      if(size!=expected||_closed)return null;
      return await temp.rename('${root.path}/${t.id}.$ext');
    }catch(_){return null;}
    finally{
      client.close();_clients.remove(client);
      if(sink!=null){try{await sink.close();}catch(_){}}
      if(await temp.exists()){try{await temp.delete();}catch(_){}}
    }
  }
  Future<void> _trim(int incoming,Set<int> protected) async {
    final files=<File>[];
    var total=0;
    await for(final entity in root.list()){
      if(entity is File&&!entity.path.endsWith('.part')){files.add(entity);total+=await entity.length();}
    }
    files.sort((a,b)=>a.lastModifiedSync().compareTo(b.lastModifiedSync()));
    for(final f in files){
      if(total+incoming<=maxCache)break;
      final id=int.tryParse(f.uri.pathSegments.last.split('.').first);
      if(protected.contains(id))continue;
      total-=await f.length();await f.delete();
    }
    if(total+incoming>maxCache)throw const FileSystemException('Cache full');
  }
  Future<void> close() async {
    _closed=true;
    for(final client in _clients.toList()){client.close();}
    await Future.wait(_jobs.values.toList());
    if(await root.exists())await root.delete(recursive:true);
  }
}
