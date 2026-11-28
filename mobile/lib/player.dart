import 'dart:async';
import 'dart:math';
import 'package:audio_service/audio_service.dart';
import 'package:just_audio/just_audio.dart';
import 'package:flutter/foundation.dart';
import 'api.dart';
import 'cache.dart';
import 'journal.dart';

class LainPlayer extends BaseAudioHandler {
  final engine=AudioPlayer();
  final changes=ValueNotifier<int>(0);
  final tracks=<Track>[];
  final preparing=<int,Track>{};
  LainApi? api;
  AudioCache? cache;
  SessionJournal? journal;
  bool offlineOnly=false;
  bool _restored=false;
  int _resumePosition=0;
  bool _repeatOne=false;
  bool get repeatOne=>_repeatOne;
  double volume=1.0;
  int index=-1;
  String? waveMode;
  String? error;
  bool busy=false;
  bool _filling=false;
  bool _warming=false;
  bool _polling=false;
  int _generation=0;
  Future<void> _tail=Future.value();
  String _playId='';
  int _listened=0;
  int _lastPosition=0;
  final _clock=Stopwatch()..start();
  int _lastTick=0;
  bool _started=false;
  bool _finished=false;
  Track? get current => index>=0&&index<tracks.length?tracks[index]:null;
  void changed(){changes.value++;}
  LainPlayer(){
    engine.playbackEventStream.listen((_)=>_publish(),onError:(Object e){error='Ошибка воспроизведения';changed();});
    engine.playerStateStream.listen((state){
      _publish();
      if(state.processingState==ProcessingState.completed&&!busy&&!_finished&&current!=null){
        unawaited(_serial(()=>_advance('complete')));
      }
    });
    engine.durationStream.listen((d){
      final item=mediaItem.value;
      if(item!=null&&d!=null)mediaItem.add(item.copyWith(duration:d));
    });
    engine.positionStream.listen(_tick);
    Timer.periodic(const Duration(seconds:5),(_){
      if(api!=null&&!busy){
        if(!offlineOnly)unawaited(journal?.flush()??Future<void>.value());
        if(!_restored&&(current!=null||waveMode!=null)){
          _persist();
          if(!offlineOnly){unawaited(_poll());unawaited(_refill());unawaited(_warm());}
        }
      }
    });
  }
  Future<void> _serial(Future<void> Function() task){
    final next=_tail.then((_)async{
      busy=true;changed();
      try{await task();error=null;}catch(e){error=e is ApiError?e.toString():'Не удалось выполнить действие';}
      finally{busy=false;_publish();_persist();}
    });
    _tail=next;
    return next;
  }
  Future<void> attach(LainApi client,AudioCache storage) => _serial(()async{
    api=client;cache=storage;_generation++;
    storage.protectedIds=()=>{if(current!=null)current!.id};
    journal=await SessionJournal.open(storage.durable,client);
    final data=await journal!.readQueue();
    if(data!=null){
      try{
        final rows=client.rows(data['tracks']).where((t)=>t.id>0&&t.playable).take(1000).toList();
        final idx=data['index'] as int;
        if(idx>=0&&idx<rows.length){
          tracks..clear()..addAll(rows);index=idx;
          offlineOnly=data['offline']==true;
          final mode=data['wave'];
          waveMode=!offlineOnly&&['balanced','discover','favorites'].contains(mode)?mode as String:null;
          _resumePosition=data['finished']==true?0:(data['position_ms'] as int).clamp(0,86400000).toInt();
          _restored=true;_finished=false;mediaItem.add(item(rows[idx]));_syncQueue();
        }
      }catch(_){}
    }
  });
  int get displayedPosition=>_restored?_resumePosition:engine.position.inMilliseconds;
  void _persist(){
    final j=journal;
    if(j==null)return;
    unawaited(j.saveQueue(List.of(tracks),index,displayedPosition,waveMode,offline:offlineOnly,
      finished:_finished).catchError((Object _){}));
  }
  Future<void> startOffline(List<Track> list,{int initial=0})async{
    final storage=cache;
    if(storage==null)return;
    final ready=<Track>[];
    for(final t in list.take(1000)){
      if(await storage.hasOffline(t.id))ready.add(t.ready());
    }
    final target=initial>=0&&initial<list.length?list[initial].id:null;
    final selected=target==null?0:ready.indexWhere((t)=>t.id==target);
    await start(ready,initial:max(0,selected),offline:true);
  }
  Future<void> toggleRepeat()=>_serial(()async{
    _repeatOne=!_repeatOne;
  });
  Future<void> setVolume(double value)async{
    volume=value.clamp(0,1).toDouble();
    await engine.setVolume(volume);changed();
  }
  MediaItem item(Track t)=>MediaItem(id:'${t.id}',title:t.title,artist:t.artist);
  void _publish(){
    final state=engine.processingState;
    playbackState.add(PlaybackState(
      controls:[MediaControl.skipToPrevious,engine.playing?MediaControl.pause:MediaControl.play,MediaControl.skipToNext,MediaControl.stop],
      systemActions:const {MediaAction.seek},
      androidCompactActionIndices:const [0,1,2],
      processingState:switch(state){
        ProcessingState.idle=>AudioProcessingState.idle,
        ProcessingState.loading=>AudioProcessingState.loading,
        ProcessingState.buffering=>AudioProcessingState.buffering,
        ProcessingState.ready=>AudioProcessingState.ready,
        ProcessingState.completed=>AudioProcessingState.completed,
      },
      playing:engine.playing,updatePosition:engine.position,
      bufferedPosition:engine.bufferedPosition,speed:engine.speed,
      queueIndex:index>=0?index:null,
      repeatMode:_repeatOne?AudioServiceRepeatMode.one:AudioServiceRepeatMode.none));
    changed();
  }
  void _syncQueue(){queue.add(tracks.map(item).toList());changed();}
  void _tick(Duration p){
    final now=_clock.elapsedMilliseconds;
    final delta=p.inMilliseconds-_lastPosition,wall=now-_lastTick;
    if(!busy&&!_finished&&current!=null&&engine.playing&&engine.processingState==ProcessingState.ready
       &&wall>0&&wall<2000&&delta>0&&delta<=wall+300){
      _listened+=min(delta,wall);
      if(!_started&&_listened>=2000){_started=true;_report('start');}
    }
    _lastTick=now;_lastPosition=p.inMilliseconds;
  }
  void _report(String event){
    final t=current,client=api;
    if(t==null||client==null||_playId.isEmpty||_listened<2000)return;
    final id=_playId,elapsed=_listened,duration=engine.duration?.inMilliseconds;
    final j=journal;
    if(j!=null){unawaited(j.report(t,id,event,elapsed,duration,send:!offlineOnly).catchError((Object _){}));}
    else{unawaited(client.report(t,id,event,elapsed,duration).catchError((Object _){}));}
  }
  void _finish(String event){if(!_finished){_finished=true;_report(event);}}
  String _newId(){
    final random=Random.secure();
    return List.generate(16,(_)=>random.nextInt(256).toRadixString(16).padLeft(2,'0')).join();
  }
  Future<void> start(List<Track> list,{int initial=0,bool offline=false})=>_serial(()async{
    final ready=list.where((t)=>t.playable).take(1000).toList();
    if(ready.isEmpty)throw const ApiError(404);
    _finish('stop');await engine.stop();
    _generation++;waveMode=null;preparing.clear();offlineOnly=offline;_restored=false;_resumePosition=0;
    tracks..clear()..addAll(ready);_syncQueue();
    await _load(initial.clamp(0,ready.length-1).toInt());
  });
  Future<void> enqueueNext(Track track)=>_serial(()async{
    if(!track.playable||offlineOnly&&await cache?.hasOffline(track.id)!=true)throw const ApiError(404);
    if(tracks.length>=1000)throw const FormatException('Queue full');
    if(current==null){
      tracks..clear()..add(track);_syncQueue();await _load(0);return;
    }
    tracks.insert(index+1,track);
    _syncQueue();
    unawaited(_warm());
  });
  Future<void> startWave(String mode)=>_serial(()async{
    final client=api;if(client==null)return;
    final data=await client.wave(mode);
    _finish('stop');await engine.stop();
    _generation++;waveMode=mode;preparing.clear();offlineOnly=false;_restored=false;_resumePosition=0;
    tracks..clear()..addAll(client.rows(data['results']).where((t)=>t.playable));
    for(final t in client.rows(data['preparing'])){preparing[t.id]=t;}
    index=-1;mediaItem.add(null);_syncQueue();
    if(tracks.isNotEmpty){await _load(0);}
    else if(preparing.isEmpty){throw const ApiError(404);}
    else {unawaited(_poll());}
  });
  Future<void> _load(int next,{int position=0})async{
    final client=api;if(client==null||next<0||next>=tracks.length)return;
    await engine.stop();
    index=next;
    if(waveMode!=null&&index>30){tracks.removeRange(0,index-10);index=10;_syncQueue();}
    final t=tracks[index];
    _playId=_newId();_listened=0;_started=false;_finished=false;_lastPosition=0;_lastTick=_clock.elapsedMilliseconds;
    mediaItem.add(item(t));
    final file=await cache?.find(t.id);
    if(offlineOnly&&(file==null||await cache?.hasOffline(t.id)!=true))throw const ApiError(404);
    await engine.setAudioSource(file!=null?AudioSource.file(file.path,tag:item(t)):
      AudioSource.uri(client.uri('/v1/tracks/${t.id}/audio'),headers:client.headers,tag:item(t)),
      initialPosition:Duration(milliseconds:position));
    _restored=false;_resumePosition=0;_lastPosition=position;_lastTick=_clock.elapsedMilliseconds;
    unawaited(engine.play().catchError((Object _){error='Аудио недоступно';changed();}));
    _publish();_persist();if(!offlineOnly){unawaited(_warm());unawaited(_refill());}
  }
  Future<void> _advance(String outcome)async{
    if(current==null||_finished)return;
    _finish(outcome);
    if(outcome=='complete'&&_repeatOne){await _load(index);return;}
    if(index+1<tracks.length){await _load(index+1);return;}
    await engine.stop();
    if(waveMode!=null){unawaited(_refill());}
  }
  @override Future<void> play()=>_serial(()async{
    if(current==null)return;
    if(_restored){await _load(index,position:_resumePosition);return;}
    if(_finished){await _load(index);return;}
    if(engine.processingState==ProcessingState.idle){await _load(index);return;}
    unawaited(engine.play().catchError((Object _){error='Аудио недоступно';changed();}));
  });
  @override Future<void> pause()=>_serial(()=>engine.pause());
  @override Future<void> seek(Duration p)=>_serial(()async{
    if(_restored){_resumePosition=p.inMilliseconds;return;}
    await engine.seek(p);_lastPosition=p.inMilliseconds;_lastTick=_clock.elapsedMilliseconds;
  });
  @override Future<void> skipToNext()=>_serial(()=>_advance('skip'));
  @override Future<void> skipToPrevious()=>_serial(()async{
    if(current==null)return;
    _finish('stop');await _load(max(0,index-1));
  });
  @override Future<void> skipToQueueItem(int i)=>_serial(()async{
    if(i<0||i>=tracks.length)return;
    _finish('stop');await _load(i);
  });
  @override Future<void> stop()=>_serial(()async{
    _finish('stop');await engine.stop();
    waveMode=null;preparing.clear();_generation++;offlineOnly=false;_restored=false;_resumePosition=0;
    tracks.clear();index=-1;mediaItem.add(null);_syncQueue();
    await super.stop();
  });
  Future<void> _warm() async {
    if(offlineOnly||_restored||_warming||busy&&current==null)return;
    _warming=true;
    final generation=_generation;
    try{
      final local=cache;
      if(local==null)return;
      final next=tracks.skip(max(0,index+1)).take(local.warmCount).toList();
      for(final t in next){
        if(generation!=_generation)break;
        await local.fetch(t,protected:{if(current!=null)current!.id,...next.map((x)=>x.id)});
      }
    }catch(_){}finally{_warming=false;}
  }
  Future<void> _refill() async {
    final client=api,mode=waveMode;
    if(client==null||mode==null||offlineOnly||_restored||_filling||tracks.length-index>5)return;
    _filling=true;
    final generation=_generation;
    try{
      final data=await client.wave(mode);
      if(generation!=_generation)return;
      final blocked=tracks.skip(max(0,index-9)).map((t)=>t.id).toSet();
      final ready=client.rows(data['results']).where((t)=>t.playable).toList();
      final next=ready.where((t)=>!blocked.contains(t.id)).toList();
      if(next.isEmpty&&_finished&&ready.isNotEmpty){next.addAll(ready);}
      for(final t in client.rows(data['preparing'])){preparing[t.id]=t;}
      if(next.isNotEmpty){
        final wasFinished=_finished;
        for(final t in next){preparing.remove(t.id);}
        tracks.addAll(next);_syncQueue();
        if(wasFinished||index<0){
          unawaited(_serial(()async{if(generation==_generation&&(_finished||index<0)&&index+1<tracks.length)await _load(index+1);}));
        }
        unawaited(_warm());
      }
    }catch(_){}finally{_filling=false;}
  }
  Future<void> _poll() async{
    final client=api;
    if(client==null||offlineOnly||_restored||_polling||preparing.isEmpty)return;
    _polling=true;final generation=_generation;
    try{
      final states=await client.prefetch(preparing.keys.take(5).toList(),status:true);
      if(generation!=_generation)return;
      for(final state in states){
        final id=state['track_id'] as int;
        final status=state['state'];
        if(status=='ready'){
          final t=preparing.remove(id);
          if(t!=null&&!tracks.skip(max(0,index-9)).any((x)=>x.id==id))tracks.add(t.ready());
        }else if(['failed','unavailable','busy','not_queued'].contains(status)){preparing.remove(id);}
      }
      _syncQueue();
      if((_finished||index<0)&&index+1<tracks.length){
        unawaited(_serial(()async{if(generation==_generation&&(_finished||index<0))await _load(index+1);}));
      }
      unawaited(_warm());
    }catch(_){}finally{_polling=false;}
  }
  Future<void> disconnect()=>_serial(()async{
    _finish('stop');await engine.stop();_generation++;
    final old=cache,j=journal;cache=null;journal=null;api=null;waveMode=null;offlineOnly=false;_restored=false;_resumePosition=0;
    if(j!=null){try{await j.close();}catch(_){}}
    tracks.clear();preparing.clear();index=-1;mediaItem.add(null);_syncQueue();
    if(old!=null){try{await old.close();}catch(_){}}
    await super.stop();
  });
}
