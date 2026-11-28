import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'api.dart';

/// Device-private durable queue snapshot and bounded playback report outbox.
/// Never stores credentials or creates playback events while restoring.
class SessionJournal{
  final Directory root;
  final LainApi api;
  Future<void> _tail=Future.value();
  bool _closed=false,_sending=false;
  final List<Map<String,dynamic>> _pending=[];
  static const maxReports=2000;
  SessionJournal._(this.root,this.api);
  File get _queue=>File('${root.path}/queue.json');
  File get _outbox=>File('${root.path}/playback-outbox.json');
  int get pendingCount=>_pending.length;
  static Future<SessionJournal> open(Directory root,LainApi api)async{
    final journal=SessionJournal._(root,api);
    await root.create(recursive:true);
    try{
      final rows=jsonDecode(await journal._outbox.readAsString()) as List;
      for(final row in rows.take(maxReports)){
        if(row is Map&&row['track_id'] is int&&row['play_id'] is String&&row['event'] is String){
          journal._pending.add(Map<String,dynamic>.from(row));
        }
      }
    }catch(_){}
    return journal;
  }
  Future<T> _serial<T>(Future<T> Function() task){
    final next=_tail.then((_)async{
      if(_closed)throw const FileSystemException('Journal closed');
      return await task();
    });
    _tail=next.then<void>((_){},onError:(Object _,StackTrace __){});
    return next;
  }
  Future<void> _write(File file,Object data) async{
    final temp=File('${file.path}.part');
    await temp.writeAsString(jsonEncode(data),flush:true);
    await temp.rename(file.path);
  }
  Future<Map<String,dynamic>?> readQueue()async{
    try{
      final data=jsonDecode(await _queue.readAsString()) as Map;
      if(data['version']!=1)return null;
      return Map<String,dynamic>.from(data);
    }catch(_){return null;}
  }
  Future<void> saveQueue(List<Track> tracks,int index,int position,String? wave,{required bool offline,required bool finished}){
    final data={'version':1,'tracks':tracks.take(1000).map((t)=>t.toJson()).toList(),
      'index':index,'position_ms':position.clamp(0,86400000),'wave':wave,'offline':offline,'finished':finished};
    return _serial(()=>_write(_queue,data));
  }
  Future<void> report(Track track,String playId,String event,int listened,int? duration)async{
    final row=<String,dynamic>{'track_id':track.id,'play_id':playId,'event':event,
      'listened_ms':listened.clamp(0,86400000),'duration_ms':duration==null||duration<=0?null:duration.clamp(1,86400000)};
    await _serial(()async{
      final next=[..._pending];
      // The server accepts a terminal event even if its start was lost.
      // Keep start before terminal if already queued; retries are idempotent.
      if(next.any((r)=>r['play_id']==playId&&r['event']==event))return;
      next.add(row);
      if(next.length>maxReports)next.removeRange(0,next.length-maxReports);
      await _write(_outbox,next);
      _pending..clear()..addAll(next);
    });
    unawaited(flush());
  }
  Future<void> flush()async{
    if(_closed||_sending)return;
    _sending=true;
    try{
      for(var i=0;i<20&&!_closed;i++){
        final row=await _serial(()=>Future.value(_pending.isEmpty?null:Map<String,dynamic>.of(_pending.first)));
        if(row==null)break;
        try{
          await api.request('POST','/v1/playback',body:row);
        }on ApiError catch(e){
          // Invalid or removed tracks cannot be delivered; network/auth failures
          // retain reports for a later retry.
          if(e.status!=404&&e.status!=422)break;
        }catch(_){break;}
        await _serial(()async{
          final next=[..._pending];
          final index=next.indexWhere((r)=>r['play_id']==row['play_id']&&r['event']==row['event']);
          if(index>=0)next.removeAt(index);
          await _write(_outbox,next);
          _pending..clear()..addAll(next);
        });
      }
    }catch(_){}finally{_sending=false;}
  }
  Future<void> close()async{
    _closed=true;
    await _tail;
  }
}
