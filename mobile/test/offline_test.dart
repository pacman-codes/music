import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:lain/api.dart';
import 'package:lain/cache.dart';
import 'package:lain/journal.dart';

void main(){
  late Directory base;
  late LainApi api;
  const a=Track(1,'Song','Artist',true,true,9,'add');
  const b=Track(2,'Other','Band',true,false,null,null);
  setUp(()async{
    base=await Directory.systemTemp.createTemp('lain-offline-test');
    api=LainApi(serverUri('https://example.com'),'test-secret','device');
  });
  tearDown(()async{
    api.client.close();
    if(await base.exists())await base.delete(recursive:true);
  });
  Future<AudioCache> open(http.Client Function() newClient)=>AudioCache.open(
    api,Directory('${base.path}/cache'),Directory('${base.path}/durable'),newClient:newClient);
  http.Response audio({int length=3})=>http.Response.bytes([1,2,3],200,
    headers:{'content-type':'audio/mpeg','content-length':'$length'});
  test('download survives reopening and clearing temporary cache',()async{
    var calls=0;
    final cache=await open(()=>MockClient((request)async{
      calls++;
      expect(request.url.host,'example.com');
      expect(request.headers['authorization'],'Bearer test-secret');
      expect(request.followRedirects,isFalse);
      return audio();
    }));
    expect(await cache.fetch(a),isNotNull);
    expect(await cache.pin(a),isNotNull);
    expect(calls,1);
    expect(await cache.fetch(b),isNotNull);
    await cache.settings(cache:256,offlineLimit:10,warm:5);
    await cache.clearTemporary();
    expect((await cache.usage())['cache'],0);
    expect((await cache.usage())['offline'],3);
    await cache.close(purge:false);
    final restored=await open(()=>MockClient((_)async=>throw StateError('No network')));
    expect(restored.cacheMiB,256);
    expect(restored.offlineGiB,10);
    expect(restored.warmCount,5);
    expect(restored.downloads.single.toJson(),a.toJson());
    expect(await restored.hasOffline(a.id),isTrue);
    expect((await restored.find(a.id))!.readAsBytesSync(),[1,2,3]);
    expect(await restored.find(b.id),isNull);
    restored.protectedIds=()=>{a.id};
    expect(await restored.unpin(a.id),isFalse);
    restored.protectedIds=()=>{};
    expect(await restored.unpin(a.id),isTrue);
    expect(await restored.hasOffline(a.id),isFalse);
    await restored.close();
    expect(await Directory('${base.path}/durable').exists(),isFalse);
  });
  test('economy cache never masquerades as an original offline download',()async{
    final requests=<String>[];
    final cache=await open(()=>MockClient((request)async{
      final quality=request.url.queryParameters['quality']!;
      requests.add(quality);
      if(quality=='economy'){
        return http.Response.bytes([9,8],200,headers:{'content-type':'audio/mp4',
          'content-length':'2','x-lain-representation':'economy'});
      }
      return audio();
    }));
    await cache.settings(cache:256,offlineLimit:5,warm:3,streamQuality:'economy');
    final reduced=await cache.fetch(a);
    expect(reduced,isNotNull);
    expect(reduced!.readAsBytesSync(),[9,8]);
    expect(await cache.find(a.id,representation:'original'),isNull);
    expect(await cache.hasOffline(a.id),isFalse);
    final original=await cache.pin(a);
    expect(original!.readAsBytesSync(),[1,2,3]);
    expect(requests,['economy','original']);
    expect(await cache.hasOffline(a.id),isTrue);
    await cache.close(purge:false);
    final restored=await open(()=>MockClient((_)async=>throw StateError('No network')));
    expect(restored.quality,'economy');
    expect((await restored.find(a.id))!.readAsBytesSync(),[1,2,3]);
    await restored.close();
  });
  test('economy pending does not download original automatically',()async{
    final requests=<String>[];
    final cache=await open(()=>MockClient((request)async{
      requests.add(request.url.queryParameters['quality']!);
      return http.Response('{}',409);
    }));
    await cache.settings(cache:256,offlineLimit:5,warm:1,streamQuality:'economy');
    expect(await cache.fetch(a),isNull);
    expect(requests,['economy']);
    expect(await cache.find(a.id),isNull);
    await cache.close();
  });
  test('invalid downloads never become playable or pinned',()async{
    final cache=await open(()=>MockClient((_)async=>audio(length:8)));
    expect(await cache.pin(a),isNull);
    expect(cache.downloads,isEmpty);
    expect(await cache.find(a.id),isNull);
    expect((await cache.usage())['offline'],0);
    await cache.close();
  });
  test('in-flight progress acknowledgement cannot erase a newer queued report',()async{
    final dir=Directory('${base.path}/race');
    final arrived=Completer<void>(),release=Completer<void>();
    final values=<int>[];
    final retryApi=LainApi(serverUri('https://example.com'),'secret','device',client:MockClient((request)async{
      final row=jsonDecode(request.body) as Map;
      values.add(row['listened_ms'] as int);
      if(values.length==1){arrived.complete();await release.future;}
      return http.Response('{"changed":true}',200);
    }));
    final journal=await SessionJournal.open(dir,retryApi);
    final id=List.filled(32,'c').join();
    await journal.report(a,id,'progress',32000,100000,send:false);
    final sending=journal.flush();
    await arrived.future.timeout(const Duration(seconds:5));
    await journal.report(a,id,'progress',62000,100000,send:false);
    release.complete();
    await sending.timeout(const Duration(seconds:5));
    expect(values,[32000,62000]);
    expect(journal.pendingCount,0);
    await journal.close();retryApi.client.close();
  });
  test('progress outbox coalesces offline reports without losing terminal outcome',()async{
    final dir=Directory('${base.path}/progress');
    final seen=<Map>[];
    final retryApi=LainApi(serverUri('https://example.com'),'secret','device',client:MockClient((request)async{
      seen.add(jsonDecode(request.body) as Map);
      return http.Response('{"changed":true}',200);
    }));
    final journal=await SessionJournal.open(dir,retryApi);
    final id=List.filled(32,'b').join();
    await journal.report(a,id,'start',2000,100000,send:false);
    await journal.report(a,id,'progress',32000,100000,send:false);
    await journal.report(a,id,'progress',62000,100000,send:false);
    await journal.report(a,id,'progress',40000,100000,send:false);
    await journal.report(a,id,'complete',100000,100000,send:false);
    expect(journal.pendingCount,3);
    await journal.close();
    final restored=await SessionJournal.open(dir,retryApi);
    await restored.flush();
    expect(seen.map((row)=>row['event']).toList(),['start','progress','complete']);
    expect(seen.map((row)=>row['listened_ms']).toList(),[2000,62000,100000]);
    expect(restored.pendingCount,0);
    await restored.close();retryApi.client.close();
  });
  test('queue snapshots and offline telemetry survive restart and retry in order',()async{
    final dir=Directory('${base.path}/state');
    final first=await SessionJournal.open(dir,api);
    await first.saveQueue([a,b],1,42000,'balanced',offline:false,finished:false);
    final id=List.filled(32,'a').join();
    await first.report(a,id,'start',2000,100000,send:false);
    await first.report(a,id,'complete',95000,100000,send:false);
    await first.report(a,id,'complete',95000,100000,send:false);
    expect(first.pendingCount,2);
    await first.close();
    var available=false;
    final seen=<String>[];
    final retryApi=LainApi(serverUri('https://example.com'),'new-secret','device',client:MockClient((request)async{
      expect(request.url.path,'/v1/playback');
      if(!available)return http.Response('{}',503);
      final row=jsonDecode(request.body) as Map;
      seen.add(row['event'] as String);
      return http.Response('{"changed":true}',200);
    }));
    final restored=await SessionJournal.open(dir,retryApi);
    expect(restored.pendingCount,2);
    final snapshot=(await restored.readQueue())!;
    expect(snapshot['index'],1);
    expect(snapshot['position_ms'],42000);
    expect(snapshot['tracks'],[a.toJson(),b.toJson()]);
    await restored.flush();
    expect(restored.pendingCount,2);
    available=true;
    await restored.flush();
    expect(seen,['start','complete']);
    expect(restored.pendingCount,0);
    await restored.close();
    retryApi.client.close();
    final data=await File('${dir.path}/playback-outbox.json').readAsString();
    expect(jsonDecode(data),isEmpty);
    expect(data, isNot(contains('secret')));
  });
}
