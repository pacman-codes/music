import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'api.dart';

const mib=1024*1024;
const gib=1024*mib;

/// Per-device LRU cache and durable explicitly downloaded music.
/// Mutations/downloads are serialized; pinned files are never LRU victims.
class AudioCache {
  final LainApi api;
  final Directory root,durable;
  final http.Client Function() newClient;
  static const maxFile=200*mib;
  static const extensions={'audio/mpeg':'mp3','audio/mp4':'m4a','audio/flac':'flac','audio/ogg':'ogg'};
  static const cacheOptions=[256,512,1024,2048,4096];
  static const offlineOptions=[1,5,10,20];
  static const warmOptions=[0,1,3,5];
  static const qualityOptions=['original','economy'];
  final Map<int,Track> _pinned={};
  final Set<http.Client> _clients={};
  final Map<String,Future<File?>> _jobs={};
  Future<void> _tail=Future.value();
  bool _closed=false;
  int cacheMiB=1024,offlineGiB=5,warmCount=3;
  String quality='original';
  Set<int> Function()? protectedIds;
  AudioCache._(this.api,this.root,this.durable,this.newClient);
  static Future<AudioCache> create(LainApi api) async {
    if(!RegExp(r'^[a-f0-9]{32}$').hasMatch(api.deviceId))throw const FormatException('Invalid device');
    final base=await getTemporaryDirectory();
    final support=await getApplicationSupportDirectory();
    return open(api,Directory('${base.path}/lain-${api.deviceId}'),
      Directory('${support.path}/lain-${api.deviceId}'));
  }
  static Future<AudioCache> open(LainApi api,Directory root,Directory durable,{http.Client Function()? newClient}) async {
    final cache=AudioCache._(api,root,durable,newClient??http.Client.new);
    await root.create(recursive:true);
    await cache.offline.create(recursive:true);
    await cache._restore();
    return cache;
  }
  Directory get offline=>Directory('${durable.path}/audio');
  File get _index=>File('${durable.path}/downloads.json');
  List<Track> get downloads=>List.unmodifiable(_pinned.values);
  bool isPinned(int id)=>_pinned.containsKey(id);
  Set<int> _protected(Set<int> explicit)=>{...explicit,...?protectedIds?.call()};
  Future<T> _serial<T>(Future<T> Function() task){
    final next=_tail.then((_)async{
      if(_closed)throw const FileSystemException('Cache closed');
      return await task();
    });
    _tail=next.then<void>((_){},onError:(Object _,StackTrace __){});
    return next;
  }
  Future<void> _restore() async {
    try{
      final data=jsonDecode(await _index.readAsString()) as Map;
      final c=data['cache_mib'],o=data['offline_gib'],w=data['warm_count'];
      if(cacheOptions.contains(c))cacheMiB=c as int;
      if(offlineOptions.contains(o))offlineGiB=o as int;
      if(warmOptions.contains(w))warmCount=w as int;
      if(qualityOptions.contains(data['quality']))quality=data['quality'] as String;
      for(final raw in (data['downloads'] as List? ?? []).take(2000)){
        try{
          final t=Track.fromJson(Map<String,dynamic>.from(raw as Map));
          if(t.id>0&&await _locate(offline,t.id)!=null)_pinned[t.id]=t.ready();
        }catch(_){}
      }
    }catch(_){}
    // Only delete interrupted downloads; preserve complete files even if index is corrupt.
    for(final dir in [root,offline]){
      await for(final e in dir.list()){
        if(e is File&&e.path.endsWith('.part')){try{await e.delete();}catch(_){}}
      }
    }
  }
  Future<void> _saveIndex(Map<int,Track> pins,{int? c,int? o,int? w,String? q}) async {
    final tmp=File('${_index.path}.part');
    await tmp.writeAsString(jsonEncode({'cache_mib':c??cacheMiB,'offline_gib':o??offlineGiB,
      'warm_count':w??warmCount,'quality':q??quality,'downloads':pins.values.map((t)=>t.toJson()).toList()}),flush:true);
    await tmp.rename(_index.path);
  }
  Future<File?> _locate(Directory dir,int id,{String representation='original'})async{
    for(final ext in extensions.values){
      final key=representation=='economy'?'$id-economy':'$id';
      final f=File('${dir.path}/$key.$ext');
      try{if(await f.exists()&&await f.length()>0)return f;}catch(_){}
    }
    return null;
  }
  Future<File?> find(int id,{String? representation}) async {
    final pinned=await _locate(offline,id);
    if(pinned!=null)return pinned;
    final f=await _locate(root,id)??((representation??quality)=='economy'?await _locate(root,id,representation:'economy'):null);
    if(f!=null){try{await f.setLastModified(DateTime.now());}catch(_){}}
    return f;
  }
  Future<bool> hasOffline(int id) async=>_pinned.containsKey(id)&&await _locate(offline,id)!=null;
  Future<int> _bytes(Directory dir)async{
    var total=0;
    await for(final e in dir.list()){
      if(e is File&&!e.path.endsWith('.part')){try{total+=await e.length();}catch(_){}}
    }
    return total;
  }
  Future<Map<String,int>> usage() async=>{'cache':await _bytes(root),'offline':await _bytes(offline)};
  Future<void> settings({required int cache,required int offlineLimit,required int warm,String? streamQuality})=>_serial(()async{
    if(!cacheOptions.contains(cache)||!offlineOptions.contains(offlineLimit)||!warmOptions.contains(warm)||!qualityOptions.contains(streamQuality??quality)){
      throw const FormatException('Unsupported storage settings');
    }
    await _saveIndex(_pinned,c:cache,o:offlineLimit,w:warm,q:streamQuality);
    cacheMiB=cache;offlineGiB=offlineLimit;warmCount=warm;quality=streamQuality??quality;
    await _trim(0,{},strict:false);
  });
  Future<void> clearTemporary()=>_serial(()async{
    await for(final e in root.list()){
      if(e is! File||e.path.endsWith('.part'))continue;
      final id=int.tryParse(e.uri.pathSegments.last.split('.').first.split('-').first);
      if(!_protected({}).contains(id)){try{await e.delete();}catch(_){}}
    }
  });
  Future<File?> fetch(Track track,{Set<int> protected=const {}}) {
    if(_closed||!track.playable)return Future.value(null);
    final selected=quality,key='${track.id}:$quality';
    return _jobs.putIfAbsent(key,()=>_serial<File?>(()async{
      final existing=await find(track.id,representation:selected);
      if(existing!=null)return existing;
      return _download(track,root,protected,pin:false,representation:selected);
    }).catchError((Object _)=>null).whenComplete((){_jobs.remove(key);}));
  }
  Future<File?> pin(Track track)=>_serial(()async{
    if(!track.playable)return null;
    if(_pinned.length>=2000&&!_pinned.containsKey(track.id))return null;
    var file=await _locate(offline,track.id);
    if(file==null){
      final cached=await _locate(root,track.id);
      if(cached!=null){
        final length=await cached.length();
        if(await _bytes(offline)+length>offlineGiB*gib)return null;
        final name=cached.uri.pathSegments.last;
        final temp=File('${offline.path}/$name.part');
        try{await cached.copy(temp.path);if(_closed)return null;
          file=await temp.rename('${offline.path}/$name');
        }finally{if(await temp.exists())await temp.delete();}
      }else{
        file=await _download(track,offline,{},pin:true);
      }
    }
    if(file==null||_closed)return null;
    final next={..._pinned,track.id:track.ready()};
    await _saveIndex(next);
    _pinned[track.id]=track.ready();
    return file;
  });
  Future<bool> unpin(int id)=>_serial(()async{
    if(_protected({}).contains(id))return false;
    final next={..._pinned}..remove(id);
    await _saveIndex(next);
    _pinned.remove(id);
    for(final ext in extensions.values){
      final f=File('${offline.path}/$id.$ext');
      if(await f.exists())await f.delete();
    }
    return true;
  });
  Future<File?> _download(Track t,Directory destination,Set<int> protected,{required bool pin,String representation='original'}) async {
    final key=representation=='economy'?'${t.id}-economy':'${t.id}';
    final temp=File('${destination.path}/$key.part');
    final client=newClient();_clients.add(client);
    IOSink? sink;
    try{
      final request=http.Request('GET',api.uri('/v1/tracks/${t.id}/audio',{'quality':representation}))
        ..followRedirects=false..headers.addAll(api.headers);
      final response=await client.send(request).timeout(const Duration(seconds:15));
      final ext=extensions[response.headers['content-type']?.split(';').first.trim()];
      final expected=response.contentLength;
      if(representation=='economy'&&!['economy','original'].contains(response.headers['x-lain-representation']))return null;
      final declared=response.headers['content-length'];
      if(response.statusCode!=200||ext==null||expected==null||expected<=0||expected>maxFile)return null;
      if(declared!=null&&int.tryParse(declared)!=expected)return null;
      if(pin){
        if(await _bytes(offline)+expected>offlineGiB*gib)return null;
      }else{await _trim(expected,{...protected,t.id});}
      sink=temp.openWrite();
      var size=0;
      final deadline=DateTime.now().add(const Duration(minutes:3));
      await for(final block in response.stream.timeout(const Duration(seconds:20))){
        if(_closed||DateTime.now().isAfter(deadline))throw const FileSystemException('Download cancelled');
        size+=block.length;
        if(size>expected||size>maxFile)throw const FileSystemException('File too large');
        sink.add(block);await sink.flush();
      }
      await sink.close();sink=null;
      if(size!=expected||_closed)return null;
      return await temp.rename('${destination.path}/$key.$ext');
    }catch(_){return null;}
    finally{
      client.close();_clients.remove(client);
      if(sink!=null){try{await sink.close();}catch(_){}}
      if(await temp.exists()){try{await temp.delete();}catch(_){}}
    }
  }
  Future<void> _trim(int incoming,Set<int> protected,{bool strict=true}) async {
    final files=<File>[];
    var total=0;
    await for(final entity in root.list()){
      if(entity is File&&!entity.path.endsWith('.part')){files.add(entity);total+=await entity.length();}
    }
    files.sort((a,b)=>a.lastModifiedSync().compareTo(b.lastModifiedSync()));
    for(final f in files){
      if(total+incoming<=cacheMiB*mib)break;
      final id=int.tryParse(f.uri.pathSegments.last.split('.').first.split('-').first);
      if(_protected(protected).contains(id))continue;
      total-=await f.length();await f.delete();
    }
    if(strict&&total+incoming>cacheMiB*mib)throw const FileSystemException('Cache full');
  }
  Future<void> close({bool purge=true}) async {
    _closed=true;
    for(final client in _clients.toList()){client.close();}
    await _tail;
    if(purge){
      if(await root.exists())await root.delete(recursive:true);
      if(await durable.exists())await durable.delete(recursive:true);
    }
  }
}
